const CLIENT_UID_KEY = 'voice_client_uid'
const CLIENT_NAME_KEY = 'voice_client_name'
const VOICE_TOKEN_KEY = 'voice_room_token'

/** 手机端设备标识：localStorage 里持久化，重连后仍是同一台设备。 */
export function getVoiceClientUid(): string {
  let uid = localStorage.getItem(CLIENT_UID_KEY)
  if (!uid) {
    uid = `ph_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`
    localStorage.setItem(CLIENT_UID_KEY, uid)
  }
  return uid
}

export function getVoiceClientName(): string {
  return localStorage.getItem(CLIENT_NAME_KEY) || ''
}

export function saveVoiceSession(roomId: string, token: string, name: string) {
  localStorage.setItem(VOICE_TOKEN_KEY, JSON.stringify({ roomId, token, at: Date.now() }))
  if (name) localStorage.setItem(CLIENT_NAME_KEY, name)
}

export function loadVoiceSession(roomId: string): string | null {
  try {
    const raw = localStorage.getItem(VOICE_TOKEN_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { roomId: string; token: string; at: number }
    if (parsed.roomId !== roomId) return null
    // 令牌 12 小时有效，超过 11 小时就重新加入，避免用到过期令牌
    if (Date.now() - parsed.at > 11 * 60 * 60 * 1000) return null
    return parsed.token
  } catch {
    return null
  }
}
