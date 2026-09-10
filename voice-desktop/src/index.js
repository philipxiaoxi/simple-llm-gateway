#!/usr/bin/env node
/**
 * index.js —— voice-desktop 入口
 *
 * 职责：配置加载 → CLI（--init/--help）→ 权限自检 → 连接主循环 → stdin 单键命令
 * 设计依据：docs/voice-input/桌面客户端.md §4/§5/§6/§10、协议参考.md §2.4
 */
import process from 'node:process'
import {
  ABANDON_REASONS,
  DEFER_MAX_MS,
  REV_FINAL,
  REV_REVISED,
  SegmentLedger,
  canReplace,
  charLen,
  describePlan,
  gateAction,
} from './segments.js'
import { clip, createLogger, oneLine } from './logger.js'
import { USAGE, initConfig, loadConfig, maskSecret, parseArgv } from './config.js'
import { applyPlan, classifyInjectError, createClipboardGuard, createInjector } from './inject.js'
import { createVoiceClient } from './ws.js'

/** 协议里没有 too_soon，映射到 timeout（协议参考.md §2.4 的 reason 枚举） */
const ABANDON_REASON_FALLBACK = { too_soon: 'timeout', committed: 'timeout', unchanged: 'timeout' }

/** 把内部原因规范化成协议允许的 reason */
function protocolReason(reason) {
  if (ABANDON_REASONS.includes(reason)) return reason
  return ABANDON_REASON_FALLBACK[reason] || 'timeout'
}

function print(line = '') {
  process.stdout.write(`${line}\n`)
}

async function main() {
  const argv = process.argv.slice(2)
  const parsedArgv = parseArgv(argv)

  // ---------- --help ----------
  if (parsedArgv.help) {
    print(USAGE)
    return 0
  }

  // ---------- --init ----------
  if (parsedArgv.init) {
    const { configPath } = loadConfig({ argv, persistUid: false })
    try {
      const res = await initConfig({ configPath })
      print(`\n✓ 配置已写入：${res.path}`)
      print('  下一步：编辑该文件填入 serverUrl / roomId / token，然后运行 `node src/index.js`')
      print('  也可以只填环境变量：VOICE_SERVER_URL / VOICE_ROOM_ID / VOICE_DESKTOP_TOKEN')
      return 0
    } catch (err) {
      print(`\n✗ ${err.message}`)
      return 1
    }
  }

  // ---------- 配置 ----------
  const { config, configPath, sources, problems, warnings, uidGenerated, configExists } = loadConfig({ argv })

  for (const w of warnings) print(`⚠ ${w}`)

  if (problems.length > 0) {
    print('\n✗ 尚未配置完成，无法启动。缺少以下配置：\n')
    for (const p of problems) print(`  · ${p}`)
    print('')
    print(`配置文件：${configPath}${configExists ? '' : '（不存在）'}`)
    print('快速开始：')
    print('  1) node src/index.js --init      # 交互式生成配置（会写好 clientUid）')
    print('  2) 或者用环境变量：')
    print('     export VOICE_SERVER_URL="wss://你的站/api/voice/desktop/connect"')
    print('     export VOICE_ROOM_ID="a1b2c3d4"')
    print('     export VOICE_DESKTOP_TOKEN="<房间令牌>"')
    print('  3) 也可以直接用参数：node src/index.js --server-url wss://… --room a1b2c3d4 --token …')
    print('')
    print('更多帮助：node src/index.js --help')
    return 2
  }

  const logger = createLogger({ logFile: config.logFile || null })
  const useColor = Boolean(process.stdout.isTTY)

  // ---------- 启动横幅 ----------
  logger.section('════ voice-desktop · 手机语音输入 → 电脑输入框 ════')
  logger.info(`服务端：${config.serverUrl}`)
  logger.info(`房间：${config.roomId} · 设备名：${config.name} · 设备 ID：${config.clientUid}`)
  logger.info(`令牌：${maskSecret(config.token)} · 配置文件：${configPath}${configExists ? '' : '（不存在，使用默认值+环境变量）'}`)
  if (uidGenerated) logger.ok('已自动生成 clientUid 并写回配置文件（重连后仍是同一台设备）')
  logger.info(
    `插入模式：${config.insertMode ? '开启' : '关闭'} · 原地替换：${config.replaceEnabled ? '开启' : '关闭'} · ` +
      `停顿窗口：${config.commitDelayMs}ms · 剪贴板还原：${config.clipboardRestore ? '开启' : '关闭'}`,
  )
  if (logger.file) logger.info(`日志文件：${logger.file}（按天轮转，保留 7 天）`)
  else logger.warn('未能启用文件日志（将只输出到控制台）')

  // ---------- 注入器 ----------
  const inj = createInjector({ logger })
  if (!inj.supported) {
    logger.error(inj.reason)
    logger.error('当前版本只支持 macOS；Windows / Linux 计划在 P2 支持。')
    return 1
  }

  // ---------- 权限自检（带超时：首次可能弹「想要控制 System Events」，不能挂死启动） ----------
  const perm = await inj.checkPermission()
  if (perm.ok) {
    logger.ok(`权限自检通过（当前前台 App：${perm.app}）`)
  } else if (perm.timedOut) {
    logger.error(`权限自检超时：${perm.error}`)
    logger.warn('屏幕上可能正弹着「想要控制 System Events」，请点「允许」后重启本程序。')
    logger.warn('仍会继续运行，但在授权前注入不会生效。')
  } else {
    logger.error(`权限自检失败：${perm.error}`)
    for (const line of String(perm.hint || '').split('\n')) logger.warn(line)
    logger.warn('仍会继续运行，但在授权前注入不会生效。')
  }

  // ---------- 运行时状态 ----------
  const ledger = new SegmentLedger()
  const clipboard = createClipboardGuard({ inj, logger, restoreDelayMs: 300 })
  const state = {
    insertMode: Boolean(config.insertMode),
    foregroundApp: perm.ok ? perm.app : '未知',
    lastInjectAt: 0,
    canInject: Boolean(perm.ok),
    roomBusy: false,
    room: null,
    phones: 0,
    desktops: 0,
    lastSegment: null,
  }
  let connectionCount = 0
  let permissionWarned = false
  let shuttingDown = false

  // ---------- 注入串行队列（避免两次注入交错） ----------
  let queue = Promise.resolve()
  function enqueue(task) {
    queue = queue.then(task).catch((err) => {
      logger.error(`处理任务失败：${err?.stack || err?.message || err}`)
    })
    return queue
  }

  /** 需要延后重试的替换（停顿窗口 / 房间忙） */
  const deferred = new Map() // segId → { timer, firstAt, reason, msg }
  /** 每个 segId 的最新快照（延后重试时用最新版） */
  const deferredMsg = new Map()

  function clearDeferred(segId) {
    const entry = deferred.get(segId)
    if (entry?.timer) clearTimeout(entry.timer)
    deferred.delete(segId)
    deferredMsg.delete(segId)
  }

  // ---------- 上行消息 ----------
  function sendAck({ segId, rev, action, text }) {
    const payload = { type: 'segment.ack', segId, rev, ok: true, action, chars: charLen(text), text }
    if (client.send(payload)) {
      logger.send(`ack ${segId} rev${rev} ${action} · ${charLen(text)} 字`)
    } else {
      logger.warn(`回执发送失败（连接不可用）：${segId} rev${rev}`)
    }
  }

  function sendAbandoned(segId, reason, extra = null) {
    const normalized = protocolReason(reason)
    const payload = { type: 'segment.abandoned', segId, reason: normalized }
    if (client.send(payload)) {
      logger.warn(`放弃替换 ${segId} · 原因 ${normalized}${extra ? `（${extra}）` : ''}`)
    }
  }

  function sendInjectError(segId, reason, detail) {
    const payload = { type: 'inject.error', segId, reason }
    if (client.send(payload)) logger.error(`注入失败 ${segId} · 原因 ${reason}${detail ? `（${detail}）` : ''}`)
  }

  /** 同一个段落、同一个原因只上报一次，避免权限缺失时刷屏 */
  const reportedInjectErrors = new Set()
  function reportInjectError(segId, reason, detail) {
    const key = `${segId}\u0000${reason}`
    if (reportedInjectErrors.has(key)) return
    reportedInjectErrors.add(key)
    if (reportedInjectErrors.size > 200) reportedInjectErrors.clear()
    sendInjectError(segId, reason, detail)
  }

  function currentStatus() {
    return {
      insertMode: state.insertMode,
      foregroundApp: state.foregroundApp,
      canInject: state.canInject,
    }
  }

  // ---------- 实际执行注入 ----------
  /**
   * 执行一次注入。**不抛异常**：失败时上报 inject.error 并返回 { ok:false }，
   * 账本保持原状，下一帧快照自然会重试。
   */
  async function perform(plan) {
    const started = Date.now()
    let result
    try {
      result = await applyPlan(plan, {
        inj,
        clipboard,
        clipboardRestore: config.clipboardRestore,
        logger: null,
      })
    } catch (err) {
      const reason = classifyInjectError(err)
      const detail = String(err?.stderr || err?.message || err).trim().slice(0, 200)
      reportInjectError(plan.segId, reason, detail)
      if (reason === 'perm_denied' || reason === 'clipboard_failed') {
        state.canInject = false
        if (!permissionWarned) {
          permissionWarned = true
          logger.error('注入被系统拦截：请到 系统设置 → 隐私与安全性 → 辅助功能 勾选运行本程序的宿主程序（如 Terminal），然后重启本程序')
        }
      }
      return { ok: false, reason, delta: 0, action: 'failed', pasted: '', error: err }
    }
    const cost = Date.now() - started
    state.lastInjectAt = Date.now()
    state.canInject = true
    if (result.delta !== 0) ledger.noteInjected(result.delta)
    ledger.setTail(plan.segId)
    state.lastSegment = { segId: plan.segId, text: plan.text, rev: plan.rev, at: Date.now() }
    logger.recv(`rev${plan.rev} 「${oneLine(clip(plan.text, 24))}」 ${describePlan(plan)} ${cost}ms`)
    return { ok: true, ...result, cost }
  }

  /** 生成「只追加」的降级动作（闸门不通过时使用） */
  function appendOnlyPlan(plan) {
    if (!plan.tailNew) return null
    return { ...plan, kind: 'append', tailOld: 0, appendOnly: true }
  }

  /**
   * 闸门不通过：只做追加（安全），绝不回改，并上报 abandoned。
   */
  async function degrade(plan, reason, { silent = false } = {}) {
    ledger.markCommitted(plan.segId)
    const seg = ledger.get(plan.segId)
    if (seg) seg.commitReason = reason

    const canAppend = state.insertMode && reason !== 'mode_off' && plan.isTail && Boolean(plan.tailNew)
    if (canAppend) {
      const fallback = appendOnlyPlan(plan)
      const res = await perform(fallback)
      if (!res.ok) {
        logger.warn(`闸门拦截（${reason}）后追加也失败（${res.reason}），下一帧快照会重试`)
        return 'failed'
      }
      ledger.commit({ segId: plan.segId, rev: plan.rev, text: plan.text, final: plan.final, injected: true })
      sendAck({ segId: plan.segId, rev: plan.rev, action: 'appended', text: plan.text })
      logger.warn(`闸门拦截（${reason}）→ 只追加 ${charLen(res.pasted)} 字，不回改`)
      if (!silent) sendAbandoned(plan.segId, reason)
      return 'appended'
    }
    ledger.commit({ segId: plan.segId, rev: plan.rev, text: plan.text, final: plan.final, injected: true })
    logger.warn(`闸门拦截（${reason}）→ 跳过该段，不回改`)
    if (!silent) sendAbandoned(plan.segId, reason)
    return 'skipped'
  }

  /**
   * 处理一条 segment.snapshot（协议里唯一的下发文本消息，永远是整段快照）。
   */
  async function handleSnapshot(msg, { fromRecent = false } = {}) {
    const segId = String(msg.segId)
    const rev = Number.isFinite(Number(msg.rev)) ? Number(msg.rev) : 0
    const text = String(msg.text ?? '')
    const final = msg.final === true
    if (typeof msg.roomBusy === 'boolean') state.roomBusy = msg.roomBusy

    // 插入模式关闭：只记账 + 打印待插入，绝不注入
    if (!state.insertMode) {
      clearDeferred(segId)
      const existing = ledger.get(segId)
      if (existing?.injected) {
        // 该段已经上过屏（用户中途关了插入模式）：只更新元数据，
        // 绝不能改回「待插入」，否则重新打开插入模式时会重复上屏一遍
        ledger.touch(segId, { rev, text, final })
        logger.pending(`[仅记录] ${oneLine(clip(text, 60))}（插入模式关闭，不回改）`)
      } else {
        ledger.markPending({ segId, rev, text, final, seq: msg.seq })
        logger.pending(`[待插入] ${oneLine(clip(text, 60))}（插入模式关闭，按 i 开启）`)
      }
      return
    }

    const plan = ledger.plan({ segId, rev, text, final, seq: msg.seq })
    if (!plan) {
      logger.info(`忽略过期/重复快照 ${segId} rev${rev}`)
      return
    }

    if (plan.kind === 'skip') {
      ledger.touch(segId, { rev, text, final })
      logger.info(`文本无变化，无需操作：${segId} rev${rev}`)
      return
    }

    // ---- 新段落：整体插入（不需要过闸门） ----
    if (plan.kind === 'insert') {
      const frozen = ledger.freezeOthers(segId)
      // 光标即将属于新段：立刻切尾部，避免异步注入期间旧段仍被当成「可回改」
      ledger.setTail(segId)
      if (frozen.length > 0) {
        logger.info(`新段落开始，前 ${frozen.length} 段不再回改：${frozen.join(', ')}`)
        for (const id of frozen) {
          const seg = ledger.get(id)
          if (seg?.injected) sendAbandoned(id, seg.commitReason || 'timeout', '新段落已开始')
          clearDeferred(id)
        }
      }
      const res = await perform(plan)
      if (!res.ok) {
        logger.warn(`插入失败（${res.reason}），账本保持不变，下一帧快照会重试`)
        return
      }
      // 整段插入后用实测光标位置重新基线化（每段只多一次 osascript，代价可接受）
      const pos = await safeCursorPos()
      if (pos >= 0) ledger.rebaselineCursor(pos)
      ledger.commit({ segId, rev, text, final, injected: true })
      sendAck({ segId, rev, action: 'inserted', text })
      if (fromRecent) logger.ok(`已补发错过的段落 ${segId}`)
      return
    }

    // ---- 已落定段落：只允许正向追加 ----
    if (plan.kind === 'append' && plan.appendOnly) {
      const reason = ledger.get(segId)?.commitReason || plan.blocked || 'timeout'
      if (!plan.isTail) {
        ledger.commit({ segId, rev, text, final, injected: true })
        logger.warn(`段落已落定且不在光标处，跳过：${segId}`)
        sendAbandoned(segId, 'cursor_mismatch', '该段已不在光标处')
        return
      }
      const res = await perform(plan)
      if (!res.ok) {
        logger.warn(`追加失败（${res.reason}），下一帧快照会重试`)
        return
      }
      ledger.commit({ segId, rev, text, final, injected: true })
      sendAck({ segId, rev, action: 'appended', text })
      sendAbandoned(segId, reason, '已落定段落只做正向追加')
      return
    }

    // ---- 纯追加：永远安全，不需要过闸门 ----
    if (plan.kind === 'append') {
      const res = await perform(plan)
      if (!res.ok) {
        logger.warn(`追加失败（${res.reason}），下一帧快照会重试`)
        return
      }
      ledger.commit({ segId, rev, text, final, injected: true })
      sendAck({ segId, rev, action: 'appended', text })
      return
    }

    // ---- 需要回改（replace / delete）：过三道闸 ----
    const cursor = await safeCursorPos()
    const gate = canReplace({
      replaceEnabled: config.replaceEnabled,
      insertMode: state.insertMode,
      roomBusy: msg.roomBusy === true,
      msSinceLastInject: Date.now() - state.lastInjectAt,
      commitDelayMs: config.commitDelayMs,
      isTail: plan.isTail,
      modeDirty: ledger.modeDirty,
      expectedPos: ledger.expectedPos,
      actualCursorPos: cursor,
    })
    const decision = gateAction(gate)

    if (decision === 'go') {
      clearDeferred(segId)
      const result = await perform(plan)
      if (!result.ok) {
        logger.warn(`替换失败（${result.reason}），账本保持不变，下一帧快照会重试`)
        return
      }
      ledger.commit({ segId, rev, text, final, injected: true })
      sendAck({ segId, rev, action: 'replaced', text })
      if (result.action === 'deleted') logger.ok(`已回删 ${result.selectedBack} 字（${segId}）`)
      return
    }

    if (decision === 'defer') {
      scheduleDeferred(plan, gate, msg)
      return
    }

    // decision === 'abandon'
    clearDeferred(segId)
    await degrade(plan, gate)
  }

  /** 光标位置（-1 表示拿不到 → 跳过检查） */
  async function safeCursorPos() {
    try {
      return await inj.cursorPos()
    } catch {
      return -1
    }
  }

  /** 停顿窗口 / 房间忙：稍后重试，而不是立刻放弃替换 */
  function scheduleDeferred(plan, gate, msg) {
    const segId = plan.segId
    const entry = deferred.get(segId)
    const firstAt = entry?.firstAt ?? Date.now()
    const waited = Date.now() - firstAt
    const budget = DEFER_MAX_MS[gate] ?? 3000

    if (entry?.timer) clearTimeout(entry.timer)

    if (waited >= budget) {
      // 等待超预算 → 降级为只追加 + 上报
      deferred.delete(segId)
      deferredMsg.delete(segId)
      logger.warn(`替换等待超时（${gate}，已等待 ${waited}ms）→ 降级为只追加`)
      enqueue(() => degrade(plan, gate))
      return
    }

    const delay = gate === 'too_soon'
      ? Math.max(20, Math.min(config.commitDelayMs - (Date.now() - state.lastInjectAt), budget - waited))
      : 500

    const timer = setTimeout(() => {
      deferred.delete(segId)
      const latest = deferredMsg.get(segId) || msg
      enqueue(() => handleSnapshot(latest))
    }, delay)
    timer.unref?.()
    deferred.set(segId, { timer, firstAt, reason: gate, msg })
    deferredMsg.set(segId, msg)
    if (!entry) logger.info(`暂停替换 ${segId}（${gate}），稍后自动重试`)
  }

  /** 房间空了：立刻重试所有挂起的替换 */
  function flushDeferred() {
    for (const [segId, entry] of [...deferred.entries()]) {
      if (entry.timer) clearTimeout(entry.timer)
      deferred.delete(segId)
      const msg = deferredMsg.get(segId)
      if (msg) enqueue(() => handleSnapshot(msg))
    }
  }

  // ---------- 服务端消息处理 ----------
  const client = createVoiceClient({
    serverUrl: config.serverUrl,
    logger,
    insecureTls: process.env.VOICE_INSECURE_TLS === '1',
    buildRegister: () => ({
      type: 'register',
      roomId: config.roomId,
      token: config.token,
      clientUid: config.clientUid,
      name: config.name,
      caps: ['inject', 'clipboard'],
      insertMode: state.insertMode,
    }),
    buildStatus: currentStatus,
    onOpenChanged: () => {},
    onAuthFail: () => {
      logger.error('鉴权失败：请核对 roomId 与 token（令牌 30 天有效期，过期需重新生成）')
    },
    onRegistered: (msg) => {
      connectionCount += 1
      state.room = msg.room || null
      const roomName = msg.room?.name || msg.room?.roomId || config.roomId
      const peers = msg.room?.phones ?? msg.roomState?.phones
      logger.ok(`已注册 · 房间「${roomName}」${Number.isFinite(peers) ? ` · 对端手机 ${peers} 台` : ''}`)
      handleRecentSegments(msg.recentSegments, { firstConnection: connectionCount === 1 })
    },
    onSnapshot: (msg) => enqueue(() => handleSnapshot(msg)),
    onAbort: (msg) => enqueue(() => handleAbort(msg)),
    onRoomState: (msg) => {
      if (typeof msg.busy === 'boolean') state.roomBusy = msg.busy
      if (Number.isFinite(msg.phones)) state.phones = msg.phones
      if (Number.isFinite(msg.desktops)) state.desktops = msg.desktops
      logger.info(
        `房间状态：${msg.busy ? '录音中' : '空闲'} · 手机 ${state.phones} 台 · 电脑 ${state.desktops} 台`,
      )
      if (msg.busy === false) flushDeferred()
    },
  })

  /**
   * 注册成功后的 recentSegments：按 segId 去重，只处理未见过的。
   *
   * 后端字段实测（voice_session.recent_segments_for_desktop）：只补发 rev>=1 的段落，
   * 结构为 { segId, seq, rev, state, rawText, polishedText, … }，**没有 text / final 字段**，
   * 所以这里统一做一次归一化。
   */
  function handleRecentSegments(recent, { firstConnection }) {
    const list = Array.isArray(recent) ? recent : []
    if (list.length === 0) return
    let seen = 0
    let restored = 0
    for (const item of list) {
      const segId = item?.segId ? String(item.segId) : null
      if (!segId) continue
      const rev = Number.isFinite(Number(item.rev)) ? Number(item.rev) : REV_FINAL
      const text = String(item.polishedText || item.rawText || item.text || '')
      if (!text) continue
      const final = item.final === true || item.state === 'revised' || rev >= REV_REVISED

      if (ledger.has(segId)) {
        // 已处理过：服务端可能没收到回执，补发一次 ack
        const seg = ledger.get(segId)
        if (seg?.injected) {
          sendAck({ segId, rev: seg.rev, action: seg.committed ? 'replaced' : 'appended', text: seg.text })
        }
        seen += 1
        continue
      }
      if (firstConnection) {
        // 首次连接：这些是本进程启动前的历史段落，绝不能重复上屏
        ledger.markHistory({ segId, rev, text, seq: item.seq, final: true })
        continue
      }
      restored += 1
      enqueue(() => handleSnapshot({ segId, seq: item.seq, rev, text, final, roomBusy: false }, { fromRecent: true }))
    }
    const parts = [`recentSegments ${list.length} 条`]
    if (seen > 0) parts.push(`已处理 ${seen} 条（跳过）`)
    if (firstConnection) parts.push('首次连接：历史段落只记录、不重复上屏')
    if (restored > 0) parts.push(`补发 ${restored} 条未见过的`)
    logger.info(parts.join(' · '))
  }

  /** segment.abort：撤掉某段（只有未落定的段允许撤销） */
  async function handleAbort(msg) {
    const segId = String(msg.segId || '')
    if (!segId) return
    clearDeferred(segId)
    const seg = ledger.get(segId)
    if (!seg) {
      logger.info(`收到撤销请求，但本地没有该段：${segId}`)
      return
    }
    if (seg.committed) {
      logger.warn(`该段已落定，无法撤销：${segId}（${msg.reason || 'abort'}）`)
      return
    }
    if (!seg.injected || !state.insertMode || !ledger.isTail(segId)) {
      logger.warn(`该段不在光标处或未注入，仅从账本移除：${segId}`)
      ledger.forget(segId)
      return
    }
    const chars = charLen(seg.text)
    try {
      await inj.selectBack(chars)
      await inj.pressDelete()
      ledger.noteInjected(-chars)
      ledger.forget(segId)
      state.lastInjectAt = Date.now()
      logger.ok(`已撤销段落 ${segId}（回删 ${chars} 字）`)
    } catch (err) {
      logger.error(`撤销失败：${err.message}`)
    }
  }

  // ---------- 前台 App / 光标巡检 ----------
  let polling = false
  const pollTimer = setInterval(async () => {
    if (polling || shuttingDown) return
    if (!state.insertMode) return
    if (ledger.openCount() === 0) return // 没有可回改的段，无需巡检
    polling = true
    try {
      const app = await inj.foregroundApp()
      if (app && app !== '未知' && state.foregroundApp !== '未知' && app !== state.foregroundApp) {
        const prev = state.foregroundApp
        state.foregroundApp = app
        logger.warn(`前台 App 变化：${prev} → ${app}，放弃未落定段落的替换权`)
        const abandoned = ledger.abandonAll('user_typed')
        ledger.invalidateCursor()
        for (const item of abandoned) sendAbandoned(item.segId, item.reason, '焦点窗口变化')
      } else if (app && app !== '未知') {
        state.foregroundApp = app
      }
    } catch {
      /* 巡检失败忽略 */
    } finally {
      polling = false
    }
  }, Math.max(150, config.pollIntervalMs))
  pollTimer.unref?.()

  // ---------- 插入模式 ----------
  function setInsertMode(on, { announce = true } = {}) {
    if (state.insertMode === on) {
      if (announce) logger.info(`插入模式已经是${on ? '开启' : '关闭'}状态`)
      return
    }
    state.insertMode = on
    if (on) {
      logger.ok('插入模式：开启（收到的文本会直接填进当前输入框；再按 i 关闭）')
      // 补上最近一条尚未注入的段落
      const pending = ledger.lastPending()
      if (pending) {
        logger.info('正在补上最近一条未注入的段落（请确认光标已在目标输入框）')
        enqueue(async () => {
          const plan = {
            kind: 'insert',
            segId: pending.segId,
            rev: pending.rev,
            final: pending.final,
            text: pending.text,
            chars: charLen(pending.text),
            tailOld: 0,
            tailNew: pending.text,
            prefix: '',
            isTail: true,
            prevText: '',
          }
          const res = await perform(plan)
          if (!res.ok) {
            logger.warn(`补发失败（${res.reason}），可稍后按 s 查看状态或重新按 i 重试`)
            return
          }
          ledger.commit({
            segId: pending.segId,
            rev: pending.rev,
            text: pending.text,
            final: pending.final,
            injected: true,
          })
          sendAck({ segId: pending.segId, rev: pending.rev, action: 'inserted', text: pending.text })
        })
      }
    } else {
      logger.warn('插入模式：关闭（只打印不注入；按 i 开启）')
      const abandoned = ledger.abandonAll('mode_off')
      for (const item of abandoned) sendAbandoned(item.segId, item.reason, '插入模式已关闭')
    }
    client.send({ type: 'status', ...currentStatus() })
  }

  // ---------- 状态打印 ----------
  function printStatus() {
    const s = ledger.stats()
    const last = state.lastSegment
    logger.section('──── 状态 ────')
    logger.info(`连接：${client.state}${client.room ? ` · 房间「${client.room.name || client.room.roomId}」` : ''} · ${config.serverUrl}`)
    logger.info(`插入模式：${state.insertMode ? '开启' : '关闭'} · 前台 App：${state.foregroundApp} · 可注入：${state.canInject ? '是' : '否'}`)
    logger.info(`房间：${state.roomBusy ? '录音中' : '空闲'} · 手机 ${state.phones} 台 · 电脑 ${state.desktops} 台 · 待重试替换 ${deferred.size} 个`)
    logger.info(
      `账本：${s.total} 段（已注入 ${s.injected} / 待插入 ${s.pending} / 已落定 ${s.committed}）· ` +
        `期望光标位置 ${s.expectedPos === null ? '未知' : s.expectedPos} · 尾部段 ${s.tailSegId || '无'}`,
    )
    if (last) logger.info(`最近一段：rev${last.rev}「${oneLine(clip(last.text, 40))}」`)
    logger.info(`配置：替换 ${config.replaceEnabled ? '开' : '关'} · 停顿窗口 ${config.commitDelayMs}ms · 剪贴板还原 ${config.clipboardRestore ? '开' : '关'}`)
  }

  // ---------- stdin 单键命令 ----------
  let stdinReady = false
  function setupStdin() {
    if (!process.stdin.isTTY) {
      logger.warn('当前 stdin 不是终端，单键命令（i/s/c/r/q）不可用；可用 Ctrl+C 退出')
      return
    }
    try {
      process.stdin.setRawMode(true)
    } catch (err) {
      logger.warn(`无法开启单键模式：${err.message}`)
      return
    }
    process.stdin.resume()
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', (chunk) => {
      for (const ch of String(chunk)) {
        handleKey(ch)
      }
    })
    stdinReady = true
  }

  function handleKey(ch) {
    switch (ch) {
      case 'i':
        setInsertMode(!state.insertMode)
        break
      case 's':
        printStatus()
        break
      case 'c':
        ledger.clear()
        deferred.clear()
        deferredMsg.clear()
        logger.ok('已清空段落账本（期望光标位置归零）')
        break
      case 'r':
        logger.info('手动重连…')
        client.reconnect()
        break
      case 'h':
      case '?':
        print('单键命令：i 切换插入模式 · s 状态 · c 清空账本 · r 重连 · q 退出')
        break
      case 'q':
      case '\u0003': // Ctrl+C
      case '\u0004': // Ctrl+D
        shutdown('收到退出指令').then(() => process.exit(0))
        break
      default:
        break
    }
  }

  // ---------- 退出 ----------
  async function shutdown(reason) {
    if (shuttingDown) return
    shuttingDown = true
    logger.info(`正在退出（${reason}）…`)
    clearInterval(pollTimer)
    for (const [segId, entry] of deferred) {
      if (entry.timer) clearTimeout(entry.timer)
      deferred.delete(segId)
    }
    clipboard.cancelRestore()
    try {
      client.stop({ code: 1000, reason: 'client exit' })
    } catch {
      /* ignore */
    }
    if (stdinReady) {
      try {
        process.stdin.setRawMode(false)
        process.stdin.pause()
      } catch {
        /* ignore */
      }
    }
    logger.info('已退出')
  }

  // ---------- 启动 ----------
  process.on('SIGINT', () => shutdown('SIGINT').then(() => process.exit(0)))
  process.on('SIGTERM', () => shutdown('SIGTERM').then(() => process.exit(0)))
  process.on('unhandledRejection', (err) => {
    logger.error(`未处理的 Promise 异常（已忽略）：${err?.stack || err}`)
  })
  process.on('uncaughtException', (err) => {
    logger.error(`未捕获异常（已忽略）：${err?.stack || err}`)
  })

  setupStdin()
  logger.info('单键命令：i 切换插入模式 · s 状态 · c 清空账本 · r 重连 · q 退出')
  logger.info(`当前插入模式：${state.insertMode ? '开启' : '关闭'}（${state.insertMode ? '收到文本会直接注入' : '收到文本只打印 [待插入]'}）`)
  client.start()

  return null // 常驻，不退出
}

main()
  .then((code) => {
    if (typeof code === 'number') process.exit(code)
  })
  .catch((err) => {
    print(`\n✗ 启动失败：${err?.stack || err?.message || err}`)
    process.exit(1)
  })
