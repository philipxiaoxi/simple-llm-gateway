import { Mic } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, clearToken } from '../lib/api'
import { checkVoiceSupport } from '../lib/voiceAudio'
import { Button, Card, Field, Input } from '../components/ui'

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

export function VoiceJoinPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [code, setCode] = useState(() => params.get('code') ?? '')
  const [pin, setPin] = useState('')
  const [name, setName] = useState(() => getVoiceClientName())
  const [room, setRoom] = useState<{ roomId: string; name: string; requirePin: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const support = useMemo(() => checkVoiceSupport(), [])

  // 手机端不共享管理端登录态，进来自动清掉，避免误带 Authorization 头
  useEffect(() => {
    clearToken()
  }, [])

  // 支持扫描/点击带 room 参数的链接直接进入
  const directRoom = params.get('room')
  useEffect(() => {
    if (!directRoom) return
    setError('')
    api
      .voiceJoin(directRoom, { clientUid: getVoiceClientUid(), name: name || '手机' })
      .then((result) => {
        saveVoiceSession(directRoom, result.token, name)
        navigate(`/voice/send/${directRoom}`, { replace: true })
      })
      .catch(() => setError('链接已失效，请手动输入房间码'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [directRoom])

  async function handleLookup() {
    const trimmed = code.trim()
    if (trimmed.length < 4) {
      setError('请输入 6 位房间码')
      return
    }
    setBusy(true)
    setError('')
    try {
      const found = await api.voiceLookup(trimmed)
      setRoom(found)
    } catch (caught) {
      setRoom(null)
      setError(caught instanceof Error ? caught.message : '房间码不正确')
    } finally {
      setBusy(false)
    }
  }

  async function handleJoin() {
    if (!room) return
    setBusy(true)
    setError('')
    try {
      const result = await api.voiceJoin(room.roomId, {
        code: code.trim(),
        pin: pin.trim() || undefined,
        clientUid: getVoiceClientUid(),
        name: name.trim() || '手机',
      })
      saveVoiceSession(room.roomId, result.token, name.trim())
      navigate(`/voice/send/${room.roomId}`, { replace: true })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加入失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-enter flex min-h-svh flex-col items-center justify-center bg-ink px-4 py-[max(1.5rem,env(safe-area-inset-top))] pb-[max(1.5rem,env(safe-area-inset-bottom))]">
      <div className="w-full max-w-md space-y-4">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-signal/15 text-signal">
            <Mic size={26} />
          </div>
          <h1 className="text-xl font-semibold text-paper">手机语音输入</h1>
          <p className="text-sm text-mist">按住说话，文字直接填进电脑的输入框</p>
        </div>

        {!support.supported && (
          <Card className="border-warn/40 bg-warn/10">
            <p className="text-sm text-warn">{support.reason}</p>
          </Card>
        )}

        <Card className="space-y-4">
          {!room ? (
            <>
              <Field label="房间码">
                <Input
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="6 位数字"
                  className="text-center text-2xl tracking-[0.4em]"
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void handleLookup()
                  }}
                />
              </Field>
              {error && <p className="text-sm text-danger">{error}</p>}
              <Button className="w-full" disabled={busy || code.trim().length < 4} onClick={() => void handleLookup()}>
                {busy ? '查询中…' : '进入房间'}
              </Button>
            </>
          ) : (
            <>
              <div className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm">
                <span className="text-mist">房间：</span>
                <span className="font-medium text-paper">{room.name}</span>
              </div>
              {room.requirePin && (
                <Field label="房间口令">
                  <Input
                    value={pin}
                    onChange={(event) => setPin(event.target.value.slice(0, 32))}
                    type="password"
                    inputMode="numeric"
                    placeholder="请输入口令"
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void handleJoin()
                    }}
                  />
                </Field>
              )}
              <Field label="设备名称（可选）">
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value.slice(0, 32))}
                  placeholder="例如：小飞的 iPhone"
                />
              </Field>
              {error && <p className="text-sm text-danger">{error}</p>}
              <div className="flex gap-2">
                <Button variant="line" onClick={() => setRoom(null)}>
                  返回
                </Button>
                <Button className="flex-1" disabled={busy} onClick={() => void handleJoin()}>
                  {busy ? '加入中…' : '加入房间'}
                </Button>
              </div>
            </>
          )}
        </Card>

        <p className="px-2 text-center text-xs leading-relaxed text-mist">
          麦克风需要 HTTPS 或本机访问才能使用。加入后请保持本页面在前台，锁屏会导致录音中断。
        </p>
      </div>
    </div>
  )
}
