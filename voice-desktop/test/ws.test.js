/**
 * ws.test.js —— 用本地假 WS 服务端验证注册、快照处理、去重、心跳与重连
 * （对应 docs/voice-input/桌面客户端.md §9 的 ws.test.js）
 * 运行：node --test
 */
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { WebSocketServer } from 'ws'
import { backoffDelay, createVoiceClient } from '../src/ws.js'
import { createNullLogger } from '../src/logger.js'

/** 起一个只在 127.0.0.1 监听的假服务端 */
async function startServer() {
  const wss = new WebSocketServer({ port: 0, host: '127.0.0.1' })
  await once(wss, 'listening')
  const { port } = wss.address()
  const received = []
  const sockets = []
  let onMessage = null

  wss.on('connection', (socket) => {
    sockets.push(socket)
    socket.on('message', (data) => {
      const msg = JSON.parse(data.toString('utf8'))
      received.push(msg)
      onMessage?.(msg, socket)
    })
  })

  return {
    url: `ws://127.0.0.1:${port}/api/voice/desktop/connect`,
    received,
    sockets,
    setHandler: (fn) => {
      onMessage = fn
    },
    /** 等某条上行消息 */
    waitFor: async (predicate, timeoutMs = 4000) => {
      const deadline = Date.now() + timeoutMs
      while (Date.now() < deadline) {
        const hit = received.find(predicate)
        if (hit) return hit
        await new Promise((r) => setTimeout(r, 20))
      }
      throw new Error(`等待消息超时，已收到：${JSON.stringify(received)}`)
    },
    close: async () => {
      for (const s of sockets) s.terminate()
      await new Promise((r) => wss.close(r))
    },
  }
}

function makeClient(server, overrides = {}) {
  const events = { registered: [], snapshots: [], aborts: [], roomState: [], authFail: 0, states: [] }
  const client = createVoiceClient({
    serverUrl: server.url,
    logger: createNullLogger(),
    buildRegister: () => ({
      type: 'register',
      roomId: 'a1b2c3d4',
      token: 'test-token',
      clientUid: 'dc_test',
      name: '测试机',
      caps: ['inject', 'clipboard'],
      insertMode: false,
    }),
    onRegistered: (m) => events.registered.push(m),
    onSnapshot: (m) => events.snapshots.push(m),
    onAbort: (m) => events.aborts.push(m),
    onRoomState: (m) => events.roomState.push(m),
    onAuthFail: () => {
      events.authFail += 1
    },
    onOpenChanged: (s) => events.states.push(s),
    random: () => 0.5,
    ...overrides,
  })
  return { client, events }
}

describe('backoffDelay：指数退避 1→2→4→8→…30s，±20% 抖动', () => {
  it('按 2 的幂增长并封顶 30s', () => {
    const noJitter = { random: () => 0.5 }
    assert.deepEqual(
      [1, 2, 3, 4, 5, 6, 7, 8].map((n) => backoffDelay(n, noJitter)),
      [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000],
    )
  })

  it('抖动在 ±20% 以内', () => {
    const low = backoffDelay(1, { random: () => 0 })
    const high = backoffDelay(1, { random: () => 1 })
    assert.equal(low, 800)
    assert.equal(high, 1200)
  })
})

describe('注册与消息分发', () => {
  it('首帧是 register，收到 registered 后进入 online', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type === 'register') {
        socket.send(
          JSON.stringify({
            type: 'registered',
            room: { roomId: 'a1b2c3d4', name: '书房' },
            recentSegments: [],
          }),
        )
      }
    })
    const { client, events } = makeClient(server)
    client.start()
    try {
      const reg = await server.waitFor((m) => m.type === 'register')
      assert.equal(reg.roomId, 'a1b2c3d4')
      assert.equal(reg.clientUid, 'dc_test')
      assert.deepEqual(reg.caps, ['inject', 'clipboard'])
      assert.equal(reg.insertMode, false)

      await server.waitFor(() => events.registered.length === 1)
      assert.equal(events.registered[0].room.name, '书房')
      assert.equal(client.state, 'online')
      assert.equal(client.room.name, '书房')
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('segment.snapshot / segment.abort / room.state 正确分发', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type === 'register') {
        socket.send(JSON.stringify({ type: 'registered', room: { roomId: 'r1' }, recentSegments: [] }))
        socket.send(
          JSON.stringify({ type: 'segment.snapshot', segId: 'seg_1', seq: 1, rev: 0, text: '你好', final: false, roomBusy: true }),
        )
        socket.send(JSON.stringify({ type: 'segment.abort', segId: 'seg_1', reason: 'user_cancelled' }))
        socket.send(JSON.stringify({ type: 'room.state', busy: false, phones: 1, desktops: 1 }))
        socket.send(JSON.stringify({ type: 'unknown.future.type', x: 1 })) // 未知类型必须被忽略
        socket.send('这不是 JSON') // 坏帧必须被忽略
      }
    })
    const { client, events } = makeClient(server)
    client.start()
    try {
      await server.waitFor(() => events.roomState.length === 1)
      assert.equal(events.snapshots.length, 1)
      assert.equal(events.snapshots[0].text, '你好')
      assert.equal(events.snapshots[0].roomBusy, true)
      assert.equal(events.aborts.length, 1)
      assert.deepEqual(events.roomState[0], { type: 'room.state', busy: false, phones: 1, desktops: 1 })
      assert.equal(client.state, 'online') // 坏帧没有把连接搞挂
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('去重：完全相同的帧只处理一次，但同 rev 的**不同文本**必须继续处理', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type !== 'register') return
      socket.send(JSON.stringify({ type: 'registered', room: {}, recentSegments: [] }))
      const frame = (text) =>
        JSON.stringify({ type: 'segment.snapshot', segId: 'seg_1', seq: 1, rev: 0, text, final: false, roomBusy: true })
      socket.send(frame('你好'))
      socket.send(frame('你好')) // 服务端重发的重复帧 → 丢弃
      socket.send(frame('你好我们')) // 同 rev=0，文本增长 → 必须处理
      socket.send(frame('你好我们明天')) // 同上
    })
    const { client, events } = makeClient(server)
    client.start()
    try {
      await server.waitFor(() => events.snapshots.length === 3, 3000)
      await new Promise((r) => setTimeout(r, 200)) // 再等一会，确认没有第 4 条
      assert.deepEqual(
        events.snapshots.map((s) => s.text),
        ['你好', '你好我们', '你好我们明天'],
      )
      assert.equal(client.seenCount(), 3)
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('心跳：每 15s 发一次 ping（测试里缩短为 60ms）', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type === 'register') socket.send(JSON.stringify({ type: 'registered', room: {}, recentSegments: [] }))
      if (msg.type === 'ping') socket.send(JSON.stringify({ type: 'pong' }))
    })
    const { client } = makeClient(server, { pingIntervalMs: 60 })
    client.start()
    try {
      await server.waitFor((m) => m.type === 'ping')
      await server.waitFor(() => server.received.filter((m) => m.type === 'ping').length >= 2)
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('send 在连接不可用时不抛异常（只返回 false）', async () => {
    const server = await startServer()
    const { client } = makeClient(server)
    assert.equal(client.send({ type: 'ping' }), false)
    assert.equal(client.send({ type: 'status', insertMode: false }), false)
    client.stop()
    await server.close()
  })

  it('上行回执格式符合协议', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type === 'register') socket.send(JSON.stringify({ type: 'registered', room: {}, recentSegments: [] }))
    })
    const { client } = makeClient(server)
    client.start()
    try {
      await server.waitFor((m) => m.type === 'register')
      client.send({ type: 'segment.ack', segId: 'seg_1', rev: 2, ok: true, action: 'replaced', chars: 10, text: '你好，我们明天见吧。' })
      const ack = await server.waitFor((m) => m.type === 'segment.ack')
      assert.deepEqual(ack, {
        type: 'segment.ack',
        segId: 'seg_1',
        rev: 2,
        ok: true,
        action: 'replaced',
        chars: 10,
        text: '你好，我们明天见吧。',
      })
      client.send({ type: 'segment.abandoned', segId: 'seg_1', reason: 'cursor_mismatch' })
      const ab = await server.waitFor((m) => m.type === 'segment.abandoned')
      assert.equal(ab.reason, 'cursor_mismatch')
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('关闭码 1008（鉴权失败）不自动重连，并回调 onAuthFail', async () => {
    const server = await startServer()
    server.setHandler((msg, socket) => {
      if (msg.type === 'register') socket.close(1008, '令牌无效')
    })
    const { client, events } = makeClient(server)
    client.start()
    try {
      await server.waitFor((m) => m.type === 'register')
      const deadline = Date.now() + 2000
      while (events.authFail === 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 20))
      assert.equal(events.authFail, 1)
      assert.equal(client.state, 'closed')
      await new Promise((r) => setTimeout(r, 1200)) // 明显超过最小退避时间
      assert.equal(server.received.filter((m) => m.type === 'register').length, 1) // 没有重连
    } finally {
      client.stop()
      await server.close()
    }
  })

  it('异常断开后按退避自动重连，并能重新注册', async () => {
    const server = await startServer()
    let firstSocket = null
    server.setHandler((msg, socket) => {
      if (msg.type !== 'register') return
      socket.send(JSON.stringify({ type: 'registered', room: {}, recentSegments: [] }))
      if (!firstSocket) {
        firstSocket = socket
        setTimeout(() => socket.close(1011, '服务端内部错误'), 30)
      }
    })
    const { client, events } = makeClient(server, { random: () => 0 }) // 抖动取下限 → 800ms 后重连
    client.start()
    try {
      await server.waitFor(() => events.registered.length >= 2, 6000)
      assert.ok(client.state === 'online')
      assert.equal(client.attempt, 0) // 重新注册成功后重置退避
    } finally {
      client.stop()
      await server.close()
    }
  })
})
