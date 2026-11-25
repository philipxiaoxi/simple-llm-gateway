import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { test } from 'node:test'
import { Worker } from './external-mcp.mjs'

test('registers, executes and reports, then re-registers after removal', async () => {
  const stop = new AbortController()
  let registrations = 0
  let claimed = false
  let reported = false
  let released = false
  let instanceId
  const errors = []
  const server = createServer(async (req, res) => {
    const send = (status, payload) => {
      res.writeHead(status, { 'Content-Type': 'application/json' })
      res.end(payload ? JSON.stringify(payload) : '')
    }
    try {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : null
      if (req.url.endsWith('/instances')) {
        assert.equal(req.headers.authorization, 'Bearer fake-registration')
        if (instanceId) assert.equal(body.instance_id, instanceId)
        instanceId = body.instance_id
        assert.equal(body.tools[0].name, 'echo')
        registrations++
        send(201, { lease_token: `lease-${registrations}`, heartbeat_seconds: 0.05 })
      } else if (req.method === 'DELETE') {
        released = true
        send(204)
      } else if (req.url.endsWith('/claims') && !claimed) {
        claimed = true
        assert.equal(req.headers.authorization, 'Bearer lease-1')
        send(200, { operation: 'echo', arguments: { text: 'test' }, dispatch_id: 'dispatch', call_id: 'call', deadline_at: new Date(Date.now() + 3000).toISOString() })
      } else if (req.url.endsWith('/result')) {
        assert.deepEqual(body, { dispatch_id: 'dispatch', ok: true, result: { text: 'test' } })
        reported = true
        send(204)
      } else if (req.headers.authorization === 'Bearer lease-1' && reported) {
        send(410, { detail: { error: { type: 'registration_missing' } } })
      } else {
        send(204)
        if (registrations === 2) stop.abort()
      }
    } catch (error) {
      errors.push(error)
      send(500)
      stop.abort()
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const worker = new Worker(`http://127.0.0.1:${server.address().port}`, 'fake-registration')
    worker.tool({ name: 'echo', description: 'Echo', input_schema: { type: 'object' } }, async ({ text }) => ({ text }))
    await worker.run({ signal: AbortSignal.any([stop.signal, AbortSignal.timeout(7000)]) })
    assert.deepEqual(errors, [])
    assert.equal(registrations, 2)
    assert(reported)
    assert(released)
  } finally {
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
})
