import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Mic, MicOff, RotateCcw, X } from 'lucide-react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'

const MAX_DURATION_MS = 60000

function pickMimeType() {
  const candidates = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
  for (const candidate of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(candidate)) {
      return candidate
    }
  }
  return ''
}

export function VoiceMobilePage() {
  const [searchParams] = useSearchParams()
  const initialCode = (searchParams.get('room') || '').trim().toUpperCase()

  const [room, setRoom] = useState(initialCode)
  const [roomName, setRoomName] = useState('')
  const [joined, setJoined] = useState(Boolean(initialCode))
  const [joining, setJoining] = useState(false)
  const [error, setError] = useState('')

  const [recording, setRecording] = useState(false)
  const [hasMic, setHasMic] = useState<boolean | null>(null)
  const [processing, setProcessing] = useState(false)
  const [transcribed, setTranscribed] = useState<{ raw_text: string; text: string; delivered: number } | null>(null)
  const [elapsed, setElapsed] = useState(0)

  const mediaRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<number | null>(null)
  const startedAtRef = useRef(0)
  const joinedRoomRef = useRef('')

  useEffect(() => {
    if (!('MediaRecorder' in window)) {
      setHasMic(false)
      return
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setHasMic(false)
      return
    }
    setHasMic(true)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      streamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])

  async function join() {
    const code = room.trim().toUpperCase()
    if (!code) {
      setError('请输入房间码')
      return
    }
    setJoining(true)
    setError('')
    try {
      const result = await api.voiceJoin(code)
      if (result.status !== 'active') {
        setError('该房间已关闭')
        return
      }
      setRoomName(result.name)
      joinedRoomRef.current = code
      setJoined(true)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加入房间失败')
    } finally {
      setJoining(false)
    }
  }

  async function startRecording() {
    if (!joined) return
    setError('')
    setTranscribed(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      const mimeType = pickMimeType()
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        void uploadRecording()
        stream.getTracks().forEach((track) => track.stop())
        streamRef.current = null
      }
      mediaRef.current = recorder
      startedAtRef.current = Date.now()
      setElapsed(0)
      recorder.start(250)
      setRecording(true)
      timerRef.current = window.setInterval(() => {
        const next = Date.now() - startedAtRef.current
        setElapsed(next)
        if (next >= MAX_DURATION_MS) stopRecording()
      }, 200)
    } catch {
      setError('无法访问麦克风，请检查浏览器权限设置')
    }
  }

  function stopRecording() {
    const recorder = mediaRef.current
    if (!recorder || recorder.state === 'inactive') return
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    setRecording(false)
    try {
      recorder.stop()
    } catch {
      /* ignore */
    }
  }

  async function uploadRecording() {
    const blob = new Blob(chunksRef.current, { type: pickMimeType() || 'audio/webm' })
    if (blob.size === 0) {
      setError('未录到声音，请重试')
      return
    }
    setProcessing(true)
    try {
      const result = await api.voiceTranscribe(joinedRoomRef.current, blob)
      setTranscribed(result)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '转写失败')
    } finally {
      setProcessing(false)
    }
  }

  function leave() {
    setJoined(false)
    setRoomName('')
    setTranscribed(null)
    setRoom('')
    setError('')
  }

  function formatDuration(ms: number) {
    const seconds = Math.floor(ms / 1000)
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  }

  return (
    <div className="flex h-svh flex-col bg-ink px-4 pt-[max(1.25rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]">
      <div className="flex items-center justify-between">
        <div className="font-mono text-xs tracking-[0.28em] text-signal">VOICE INPUT</div>
        {joined ? (
          <button type="button" onClick={leave} className="inline-flex items-center gap-1 rounded-md px-3 py-2 text-sm text-mist hover:text-paper">
            <X size={16} />
            退出房间
          </button>
        ) : (
          <Link to="/" className="inline-flex items-center gap-1 rounded-md px-3 py-2 text-sm text-mist hover:text-paper">
            <RotateCcw size={16} />
            返回
          </Link>
        )}
      </div>

      {!joined ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-1">
            <div className="w-full max-w-md rounded-xl border border-line bg-panel/90 p-5">
              <h1 className="text-xl font-semibold">加入语音输入房间</h1>
              <p className="mt-1 text-sm text-mist">输入管理后台「语音输入」页面创建的房间码，加入后即可按住说话。</p>
              <input
                value={room}
                onChange={(event) => setRoom(event.target.value.toUpperCase())}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void join()
                }}
                placeholder="例如 A8K3PZ"
                maxLength={12}
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
                className="mt-4 w-full rounded-md border border-line bg-ink px-3 py-3 text-center font-mono text-2xl tracking-[0.35em] text-paper outline-none placeholder:text-sm placeholder:tracking-normal placeholder:text-mist/70 focus:border-signal/70"
              />
              {error ? <div className="mt-3 text-sm text-danger">{error}</div> : null}
              <button
                type="button"
                onClick={() => void join()}
                disabled={joining}
                className="mt-4 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-md bg-signal px-3 py-2 text-sm font-medium text-ink transition hover:brightness-110 disabled:opacity-40"
              >
                {joining ? '加入中…' : '加入房间'}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex min-h-full flex-col items-center justify-center gap-5 p-1">
            <div className="text-center">
              <div className="text-xs uppercase tracking-[0.2em] text-mist">房间 {joinedRoomRef.current}</div>
              {roomName ? <div className="mt-1 text-lg font-semibold">{roomName}</div> : null}
            </div>

            {transcribed ? (
              <div className="w-full max-w-md space-y-3">
                <div className="rounded-xl border border-line bg-panel/90 p-4">
                  <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">语音转写</div>
                  <div className="break-words text-paper">{transcribed.raw_text}</div>
                </div>
                <div className="rounded-xl border border-signal/30 bg-signal/5 p-4">
                  <div className="mb-1 text-xs uppercase tracking-[0.16em] text-signal">优化表达</div>
                  <div className="break-words text-paper">{transcribed.text}</div>
                </div>
                <div className="text-center text-xs text-mist">
                  {transcribed.delivered > 0 ? `已推送到 ${transcribed.delivered} 个桌面端` : '桌面端未连接，文本已保存在房间记录'}
                </div>
                <button
                  type="button"
                  onClick={() => setTranscribed(null)}
                  className="inline-flex min-h-10 w-full items-center justify-center rounded-md border border-line bg-panel-2 text-sm text-paper hover:border-mist/40"
                >
                  再说一次
                </button>
              </div>
            ) : (
              <button
                type="button"
                disabled={processing}
                onPointerDown={() => {
                  if (!processing) void startRecording()
                }}
                onPointerUp={stopRecording}
                onPointerLeave={stopRecording}
                onPointerCancel={stopRecording}
                onContextMenu={(event) => event.preventDefault()}
                className={cn(
                  'relative flex h-44 w-44 shrink-0 touch-none select-none items-center justify-center rounded-full border transition-all duration-150 select-none',
                  recording
                    ? 'scale-105 border-danger bg-danger/15 text-danger'
                    : 'border-signal/40 bg-signal/10 text-signal active:scale-95',
                  processing && 'cursor-wait opacity-50',
                  hasMic === false && 'opacity-40',
                )}
                style={{ touchAction: 'none', WebkitUserSelect: 'none', userSelect: 'none' }}
              >
                <div className="flex flex-col items-center gap-2">
                  {processing ? (
                    <div className="h-10 w-10 animate-spin rounded-full border-2 border-signal/30 border-t-signal" />
                  ) : recording ? (
                    <MicOff size={40} />
                  ) : (
                    <Mic size={40} />
                  )}
                  <div className="text-sm font-medium">
                    {processing ? '正在转写' : recording ? formatDuration(elapsed) : '按住说话'}
                  </div>
                </div>
                {recording ? (
                  <span className="absolute inset-0 -z-10 animate-ping rounded-full bg-danger/10" />
                ) : null}
              </button>
            )}

            {!recording && !transcribed && hasMic === false ? (
              <div className="w-full max-w-md text-center text-sm text-danger">
                当前浏览器不支持录音（需要 MediaRecorder API 且需 HTTPS 环境）。请用手机浏览器打开此页面。
              </div>
            ) : null}
            {error && !recording ? <div className="w-full max-w-md break-words text-center text-sm text-danger">{error}</div> : null}
          </div>
        </div>
      )}
    </div>
  )
}
