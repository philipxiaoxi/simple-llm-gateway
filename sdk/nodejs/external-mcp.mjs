import { randomUUID } from 'node:crypto'
import { setTimeout as sleep } from 'node:timers/promises'

class PlatformError extends Error {
  constructor(status, code) {
    super(`External MCP API returned HTTP ${status}`)
    this.status = status
    this.code = code
  }
}

export class Worker {
  #credential
  #tools = new Map()
  #lease = null
  #running = false

  constructor(platformUrl, credential, { version = '1.0.0', description = '' } = {}) {
    const url = new URL(platformUrl)
    if (url.username || url.password || url.search || url.hash || (url.protocol !== 'https:' &&
      !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) {
      throw new Error('Use HTTPS or a loopback HTTP URL')
    }
    this.url = url.href.replace(/\/+$/, '') + '/v1/external-mcp'
    this.#credential = credential
    this.instanceId = randomUUID()
    this.version = version
    this.description = description
  }

  tool(definition, handler) {
    if (this.#running) throw new Error('Register tools before starting the worker')
    if (this.#tools.has(definition.name)) throw new Error(`Duplicate tool: ${definition.name}`)
    this.#tools.set(definition.name, { definition: structuredClone(definition), handler })
    return this
  }

  async #request(method, path, signal, { registration = false, body, timeout = 25_000 } = {}) {
    const token = registration ? this.#credential : this.#lease.lease_token
    const response = await fetch(this.url + path, {
      method, redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(timeout)]),
      headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) {
      const error = await response.json().catch(() => null)
      throw new PlatformError(response.status, error?.detail?.error?.type)
    }
    return response.status === 204 ? null : response.json()
  }

  async #heartbeat(root, signal) {
    while (!signal.aborted) {
      await sleep(this.#lease.heartbeat_seconds * 1000, undefined, { signal })
      this.#lease = await this.#request('PUT', `${root}/lease`, signal)
    }
  }

  async #execute(call, signal) {
    const remaining = Date.parse(call.deadline_at) - Date.now()
    if (!(remaining > 0)) return { ok: false, error: { type: 'timeout' } }
    const deadline = AbortSignal.timeout(Math.ceil(remaining))
    const abort = AbortSignal.any([signal, deadline])
    let onAbort
    try {
      const stopped = new Promise((_, reject) => {
        onAbort = () => reject(abort.reason)
        abort.addEventListener('abort', onAbort, { once: true })
        if (abort.aborted) onAbort()
      })
      const result = await Promise.race([stopped, Promise.resolve().then(() => {
        abort.throwIfAborted()
        return this.#tools.get(call.operation).handler(call.arguments, {
          signal: abort, callId: call.call_id, deadlineAt: call.deadline_at,
        })
      })])
      if (!result || typeof result !== 'object' || Array.isArray(result)) throw new TypeError('Return a JSON object')
      const normalized = JSON.parse(JSON.stringify(result))
      if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) throw new TypeError('Return a JSON object')
      return { ok: true, result: normalized }
    } catch {
      signal.throwIfAborted()
      return { ok: false, error: { type: deadline.aborted ? 'timeout' : 'external_error' } }
    } finally {
      abort.removeEventListener('abort', onAbort)
    }
  }

  async #work(root, signal) {
    while (!signal.aborted) {
      const call = await this.#request('POST', `${root}/claims`, signal)
      if (!call) continue
      const report = { dispatch_id: call.dispatch_id, ...await this.#execute(call, signal) }
      const path = `${root}/calls/${call.call_id}/result`
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await this.#request('PUT', path, signal, { body: report })
          break
        } catch (error) {
          if (error instanceof PlatformError && error.status === 410 && error.code === 'call_finished') break
          if (signal.aborted || attempt || error instanceof PlatformError) throw error
        }
      }
    }
  }

  async run({ signal = new AbortController().signal } = {}) {
    if (this.#running || !this.#tools.size) throw new Error('Start one worker with at least one tool')
    this.#running = true
    const root = `/instances/${this.instanceId}`
    const manifest = { protocol_version: 'external-mcp/v1', instance_id: this.instanceId,
      version: this.version, description: this.description, tools: [...this.#tools.values()].map(({ definition }) => definition) }
    let delay = 1000
    try {
      while (!signal.aborted) {
        const connection = new AbortController()
        const connected = AbortSignal.any([connection.signal, signal])
        let tasks = []
        try {
          this.#lease = await this.#request('POST', '/instances', connected, { registration: true, body: manifest })
          delay = 1000
          tasks = [this.#heartbeat(root, connected), this.#work(root, connected)]
          await Promise.all(tasks)
        } catch (error) {
          if (signal.aborted) break
          if (!tasks.length && error instanceof PlatformError && [400, 401, 403, 404, 413, 422].includes(error.status)) throw error
          console.warn('External MCP connection interrupted; retrying')
        } finally {
          connection.abort()
          await Promise.allSettled(tasks)
        }
        if (!signal.aborted) await sleep(delay + Math.random() * 500, undefined, { signal })
        delay = Math.min(20_000, delay * 2)
      }
    } catch (error) {
      if (!signal.aborted) throw error
    } finally {
      if (this.#lease) await this.#request('DELETE', `${root}/lease`, new AbortController().signal, { timeout: 3000 }).catch(() => {})
      this.#lease = null
      this.#running = false
    }
  }
}
