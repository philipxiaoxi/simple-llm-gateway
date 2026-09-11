/**
 * ws.js —— 桌面端 WebSocket 客户端
 *
 * 职责（协议参考.md §2.4）：
 *  - 首帧 register 注册；15s 心跳 ping（服务端不主动 ping）
 *  - 指数退避重连 1s→2s→4s→8s→上限 30s，±20% 抖动
 *  - 按 (segId, rev) 去重，服务端可安全重发
 *  - 任何发送失败都不能让进程崩溃
 */
import WebSocket from 'ws'

export const PING_INTERVAL_MS = 15000
export const PONG_TIMEOUT_MS = 90000
export const BACKOFF_BASE_MS = 1000
export const BACKOFF_MAX_MS = 30000
export const JITTER_RATIO = 0.2

/** 关闭码中文说明（协议参考.md §2.6） */
export const CLOSE_REASONS = Object.freeze({
  1000: '正常关闭',
  1006: '网络异常断开',
  1008: '鉴权失败 / 令牌与房间不符 / 角色不符',
  1011: '服务端内部错误',
})

/**
 * 第 attempt 次重连的等待时长（attempt 从 1 开始）。
 * 1s → 2s → 4s → 8s → 16s → 30s（封顶），带 ±20% 抖动。
 */
export function backoffDelay(attempt, { base = BACKOFF_BASE_MS, max = BACKOFF_MAX_MS, jitter = JITTER_RATIO, random = Math.random } = {}) {
  const n = Math.max(1, Math.floor(attempt))
  const raw = Math.min(base * 2 ** (n - 1), max)
  const factor = 1 + (random() * 2 - 1) * jitter
  return Math.max(50, Math.round(raw * factor))
}

/** 把任意值安全地转成 JSON 文本 */
function safeStringify(msg) {
  try {
    return JSON.stringify(msg)
  } catch {
    return null
  }
}

/**
 * 创建 WebSocket 客户端。
 *
 * @param {object} opts
 * @param {string} opts.serverUrl 完整 wss:// 地址
 * @param {() => object} opts.buildRegister 生成 register 首帧
 * @param {object} opts.logger
 * @param {(msg:object)=>void} [opts.onRegistered]
 * @param {(msg:object)=>void} [opts.onSnapshot]
 * @param {(msg:object)=>void} [opts.onAbort]
 * @param {(msg:object)=>void} [opts.onRoomState]
 * @param {() => object|null} [opts.buildStatus] 心跳周期要上报的 status
 * @param {(info:object)=>void} [opts.onAuthFail] 1008 鉴权失败
 * @param {()=>void} [opts.onOpenChanged] 连接状态变化回调
 */
export function createVoiceClient({
  serverUrl,
  buildRegister,
  logger,
  onRegistered = null,
  onSnapshot = null,
  onAbort = null,
  onRoomState = null,
  buildStatus = null,
  onAuthFail = null,
  onOpenChanged = null,
  WebSocketImpl = WebSocket,
  now = () => Date.now(),
  random = Math.random,
  pingIntervalMs = PING_INTERVAL_MS,
  pongTimeoutMs = PONG_TIMEOUT_MS,
  insecureTls = false,
  seenLimit = 1000,
} = {}) {
  let ws = null
  let state = 'idle' // idle | connecting | online | reconnecting | closed
  let attempt = 0
  let stopping = false
  let reconnectTimer = null
  let pingTimer = null
  let lastRecvAt = 0
  let lastPongAt = 0
  let registeredAt = 0
  let room = null
  let warnedSilence = false
  let sentStatusAt = 0
  let statusSentValue = null
  let lastClose = null

  /**
   * (segId, rev) 去重表；FIFO 淘汰。
   *
   * ⚠️ 去重键必须带上 text：rev 是**状态档位**（0=中间结果 / 1=ASR终稿 / 2=纠错终稿），
   * 同一段会以 rev=0 反复下发、文本不断增长（后端 `_on_partial` 每 ≥120ms 一次）。
   * 只用 (segId, rev) 去重会把中间结果全部丢掉，只剩第一帧和终稿。
   * 带上 text 后，服务端原样重发的重复帧仍然被幂等丢弃。
   */
  const seen = new Map()

  function markSeen(segId, rev, text) {
    const key = `${segId}\u0000${rev}\u0000${text ?? ''}`
    if (seen.has(key)) return false
    seen.set(key, true)
    if (seen.size > seenLimit) {
      const oldest = seen.keys().next().value
      seen.delete(oldest)
    }
    return true
  }

  function setState(next) {
    if (state === next) return
    state = next
    try {
      onOpenChanged?.(state)
    } catch (err) {
      logger?.warn(`状态回调异常：${err.message}`)
    }
  }

  /** 发送一帧；永不抛异常 */
  function send(msg) {
    if (!ws || ws.readyState !== WebSocketImpl.OPEN) return false
    const text = safeStringify(msg)
    if (text === null) {
      logger?.warn('上行消息无法序列化，已丢弃')
      return false
    }
    try {
      ws.send(text, (err) => {
        if (err) logger?.warn(`上行消息发送失败（已忽略）：${err.message}`)
      })
      return true
    } catch (err) {
      logger?.warn(`上行消息发送失败（已忽略）：${err.message}`)
      return false
    }
  }

  function stopPing() {
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
  }

  function startPing() {
    stopPing()
    lastPongAt = now()
    warnedSilence = false
    pingTimer = setInterval(() => {
      if (!ws || ws.readyState !== WebSocketImpl.OPEN) return
      send({ type: 'ping' })
      const idle = now() - Math.max(lastRecvAt, lastPongAt)
      if (idle > pongTimeoutMs) {
        logger?.warn(`已 ${Math.round(idle / 1000)}s 未收到任何服务端消息，判定连接已死，强制重连`)
        try {
          ws.terminate()
        } catch {
          /* ignore */
        }
        return
      }
      if (!warnedSilence && idle > pongTimeoutMs / 2) {
        warnedSilence = true
        logger?.warn(`已有 ${Math.round(idle / 1000)}s 未收到 pong，请检查服务端心跳应答`)
      }
      // status：每 30s 或状态变化时上报
      if (buildStatus) {
        let payload = null
        try {
          payload = buildStatus()
        } catch (err) {
          logger?.warn(`构造 status 失败：${err.message}`)
        }
        if (payload) {
          const value = JSON.stringify([payload.insertMode, payload.foregroundApp, payload.canInject])
          if (value !== statusSentValue || now() - sentStatusAt >= 30000) {
            if (send({ type: 'status', ...payload })) {
              statusSentValue = value
              sentStatusAt = now()
            }
          }
        }
      }
    }, pingIntervalMs)
    pingTimer.unref?.()
  }

  function scheduleReconnect(reasonText) {
    if (stopping) return
    attempt += 1
    const delay = backoffDelay(attempt, { random })
    setState('reconnecting')
    logger?.warn(`${reasonText} · ${(delay / 1000).toFixed(1)}s 后重连（第 ${attempt} 次）`)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
    reconnectTimer.unref?.()
  }

  function handleMessage(data) {
    lastRecvAt = now()
    let msg = null
    const text = typeof data === 'string' ? data : data?.toString?.('utf8')
    if (typeof text !== 'string') return
    try {
      msg = JSON.parse(text)
    } catch {
      logger?.warn(`收到无法解析的消息：${text.slice(0, 120)}`)
      return
    }
    if (!msg || typeof msg.type !== 'string') return

    switch (msg.type) {
      case 'registered': {
        registeredAt = now()
        attempt = 0 // 注册成功才重置退避
        room = msg.room || null
        setState('online')
        try {
          onRegistered?.(msg)
        } catch (err) {
          logger?.error(`处理 registered 失败：${err.stack || err.message}`)
        }
        break
      }
      case 'segment.snapshot': {
        if (!msg.segId) {
          logger?.warn('收到缺少 segId 的 segment.snapshot，已忽略')
          break
        }
        const rev = Number.isFinite(Number(msg.rev)) ? Number(msg.rev) : 0
        if (!markSeen(msg.segId, rev, msg.text)) break // 幂等：原样重发的重复帧直接丢
        try {
          onSnapshot?.(msg)
        } catch (err) {
          logger?.error(`处理 segment.snapshot 失败：${err.stack || err.message}`)
        }
        break
      }
      case 'segment.abort': {
        try {
          onAbort?.(msg)
        } catch (err) {
          logger?.error(`处理 segment.abort 失败：${err.stack || err.message}`)
        }
        break
      }
      case 'room.state': {
        try {
          onRoomState?.(msg)
        } catch (err) {
          logger?.error(`处理 room.state 失败：${err.stack || err.message}`)
        }
        break
      }
      case 'ping': {
        // 协议里服务端不主动 ping，但保持兼容
        send({ type: 'pong' })
        break
      }
      case 'pong': {
        lastPongAt = now()
        break
      }
      default:
        break // 未知类型静默忽略（向后兼容）
    }
  }

  function connect() {
    if (stopping) return
    setState(attempt > 0 ? 'reconnecting' : 'connecting')
    logger?.info(`正在连接 ${serverUrl} …`)
    let socket
    try {
      socket = new WebSocketImpl(serverUrl, {
        handshakeTimeout: 10000,
        maxPayload: 1 << 20,
        rejectUnauthorized: !insecureTls,
      })
    } catch (err) {
      scheduleReconnect(`建立连接失败：${err.message}`)
      return
    }
    ws = socket
    lastRecvAt = now()

    socket.on('open', () => {
      logger?.ok(`已连接 ${serverUrl}`)
      const frame = buildRegister?.()
      if (!frame) {
        logger?.error('缺少注册信息，无法注册')
        return
      }
      // 首帧必须是 register
      if (send(frame)) logger?.send(`register（clientUid=${frame.clientUid}）`)
      startPing()
    })

    socket.on('message', (data) => handleMessage(data))

    socket.on('error', (err) => {
      logger?.warn(`连接错误：${err?.message || err}`)
    })

    socket.on('close', (code, reasonBuf) => {
      const reason = reasonBuf ? reasonBuf.toString('utf8') : ''
      lastClose = { code, reason, at: now() }
      stopPing()
      ws = null
      if (stopping) {
        setState('closed')
        return
      }
      if (code === 1008) {
        setState('closed')
        logger?.error(`注册/鉴权失败（关闭码 1008：${CLOSE_REASONS[1008]}）${reason ? ` · ${reason}` : ''}`)
        logger?.error('请检查 roomId 与 token 是否匹配、令牌是否过期，修正后重新启动（或按 r 重连）')
        try {
          onAuthFail?.({ code, reason })
        } catch {
          /* ignore */
        }
        return
      }
      scheduleReconnect(`连接已断开（${code} ${CLOSE_REASONS[code] || '未知'}${reason ? ` · ${reason}` : ''}）`)
    })
  }

  return {
    start() {
      stopping = false
      attempt = 0
      connect()
    },
    /** 手动重连（鉴权失败后修正配置或令牌时使用） */
    reconnect({ reset = true } = {}) {
      if (ws) {
        try {
          ws.close(1000, 'client reconnect')
        } catch {
          /* ignore */
        }
      }
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (reset) attempt = 0
      stopping = false
      connect()
    },
    stop({ code = 1000, reason = 'client exit' } = {}) {
      stopping = true
      stopPing()
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (ws) {
        try {
          ws.close(code, reason)
        } catch {
          /* ignore */
        }
      }
      setState('closed')
    },
    send,
    isOpen: () => Boolean(ws) && ws.readyState === WebSocketImpl.OPEN,
    get state() {
      return state
    },
    get attempt() {
      return attempt
    },
    get room() {
      return room
    },
    get lastClose() {
      return lastClose
    },
    get registeredAt() {
      return registeredAt
    },
    pingIntervalMs,
    /** 仅供测试：暴露去重表大小 */
    seenCount: () => seen.size,
  }
}
