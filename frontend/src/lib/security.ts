import { getToken } from './api'

// 安全中心前端埋点：关键行为 + 运行环境指纹。内容不含任何凭据。
type BeaconEvent = {
  event: string
  path?: string
  client_time?: number
  timezone?: string
  language?: string
  screen_w?: number
  screen_h?: number
  dpr?: number
  platform?: string
  webdriver?: boolean
  action?: string
  target?: string
}

const BUFFER_LIMIT = 10
const buffer: BeaconEvent[] = []

function clientEnv(): Partial<BeaconEvent> {
  return {
    timezone: (() => {
      try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone
      } catch {
        return undefined
      }
    })(),
    language: typeof navigator !== 'undefined' ? navigator.language : undefined,
    screen_w: typeof screen !== 'undefined' ? screen.width : undefined,
    screen_h: typeof screen !== 'undefined' ? screen.height : undefined,
    dpr: typeof window !== 'undefined' ? window.devicePixelRatio : undefined,
    platform: typeof navigator !== 'undefined' ? navigator.platform : undefined,
    webdriver: typeof navigator !== 'undefined' ? navigator.webdriver === true : undefined,
  }
}

export function track(event: string, detail: Partial<BeaconEvent> = {}) {
  buffer.push({
    event,
    path: typeof location !== 'undefined' ? location.pathname : undefined,
    client_time: Date.now(),
    ...clientEnv(),
    ...detail,
  })
  if (buffer.length >= BUFFER_LIMIT) flush()
}

export function flush() {
  if (!buffer.length) return
  const token = getToken()
  if (!token) {
    buffer.length = 0
    return
  }
  const events = buffer.splice(0)
  // keepalive 允许页面卸载期间完成请求，同时能带 Authorization 头
  fetch('/api/admin/security/beacon', {
    method: 'POST',
    keepalive: true,
    headers: { 'Content-Type': 'application/json', 'X-GW-Client': 'web', Authorization: `Bearer ${token}` },
    body: JSON.stringify(events),
  }).catch(() => {})
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', flush)
}

export type { BeaconEvent }
