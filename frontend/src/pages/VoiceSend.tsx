import { ArrowLeft, Check, Copy, Loader2, Mic, MicOff, Sparkles, Trash2, Wifi, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { voiceSocketUrl } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn } from '../lib/utils'
import { createVoiceRecorder } from '../lib/voiceAudio'
import type { VoiceRecorder } from '../lib/voiceAudio'
import { getVoiceClientName, getVoiceClientUid, loadVoiceSession } from './VoiceJoin'

type Sentence = {
  segId: string
  seq: number
  text: string
  /** partial=还在变；final=ASR 定稿；revised=AI 已纠错 */
  state: 'partial' | 'final' | 'revised'
  polishMs?: number
  acked?: boolean
}

type ConnectionState = 'connecting' | 'online' | 'offline'

const MAX_SENTENCES = 60

export function VoiceSendPage() {
  const { roomId = '' } = useParams()
  const navigate = useNavigate()

  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [roomName, setRoomName] = useState('')
  const [asrModel, setAsrModel] = useState('')
  const [polishMode, setPolishMode] = useState('off')
  const [maxSeconds, setMaxSeconds] = useState(120)
  const [roomState, setRoomState] = useState({ busy: false, phones: 0, desktops: 0 })
  const [sentences, setSentences] = useState<Sentence[]>([])
  const [recording, setRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState('')
  const [cancelling, setCancelling] = useState(false)

  const socketRef = useRef<WebSocket | null>(null)
  const recorderRef = useRef<VoiceRecorder | null>(null)
  const sessionRef = useRef<string | null>(null)
  const recordingRef = useRef(false)
  const cancelRef = useRef(false)
  const startYRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const reconnectRef = useRef(0)
  const reconnectTimerRef = useRef<number | null>(null)
  const heartbeatRef = useRef<number | null>(null)
  const closedByUsRef = useRef(false)
  const listRef = useRef<HTMLDivElement | null>(null)

  const token = useMemo(() => loadVoiceSession(roomId), [roomId])

  // ---------- WebSocket ----------

  const send = useCallback((payload: Record<string, unknown>) => {
    const socket = socketRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload))
    }
  }, [])

  const connect = useCallback(() => {
    if (!token) return
    setConnection('connecting')
    const socket = new WebSocket(voiceSocketUrl(`/api/voice/rooms/${roomId}/phone`, token))
    socket.binaryType = 'arraybuffer'
    socketRef.current = socket

    socket.onopen = () => {
      reconnectRef.current = 0
      socket.send(
        JSON.stringify({
          type: 'hello',
          clientUid: getVoiceClientUid(),
          name: getVoiceClientName() || '手机',
          sampleRate: 16000,
          format: 'pcm_s16le',
        }),
      )
      if (heartbeatRef.current) window.clearInterval(heartbeatRef.current)
      heartbeatRef.current = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }))
      }, 15000)
    }

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') return
      let message: Record<string, unknown>
      try {
        message = JSON.parse(event.data) as Record<string, unknown>
      } catch {
        return
      }
      handleMessageRef.current(message)
    }

    socket.onclose = () => {
      if (heartbeatRef.current) window.clearInterval(heartbeatRef.current)
      heartbeatRef.current = null
      setConnection('offline')
      stopRecording(false)
      if (closedByUsRef.current) return
      // 指数退避重连
      const attempt = (reconnectRef.current += 1)
      const delay = Math.min(30000, 1000 * 2 ** (attempt - 1)) * (0.8 + Math.random() * 0.4)
      reconnectTimerRef.current = window.setTimeout(() => connect(), delay)
    }

    socket.onerror = () => setConnection('offline')
  }, [roomId, token, send])

  const handleMessageRef = useRef<(message: Record<string, unknown>) => void>(() => {})

  const handleMessage = useCallback((message: Record<string, unknown>) => {
    const type = message.type as string
    if (type === 'ready') {
      setConnection('online')
      const room = message.room as { name?: string } | undefined
      setRoomName(room?.name ?? '')
      setAsrModel((message.asrModel as string) ?? '')
      setPolishMode((message.polishMode as string) ?? 'off')
      setMaxSeconds((message.maxRecordingSeconds as number) ?? 120)
      setRoomState((message.roomState as typeof roomState) ?? { busy: false, phones: 1, desktops: 0 })
      return
    }
    if (type === 'room.state') {
      setRoomState({
        busy: Boolean(message.busy),
        phones: Number(message.phones ?? 0),
        desktops: Number(message.desktops ?? 0),
      })
      return
    }
    if (type === 'session.started') {
      sessionRef.current = (message.sessionUid as string) ?? null
      return
    }
    if (type === 'asr.partial') {
      upsertSentence({
        segId: message.segId as string,
        seq: Number(message.seq),
        text: (message.text as string) ?? '',
        state: 'partial',
      })
      return
    }
    if (type === 'asr.final') {
      upsertSentence({
        segId: message.segId as string,
        seq: Number(message.seq),
        text: (message.text as string) ?? '',
        state: 'final',
      })
      return
    }
    if (type === 'segment.revised') {
      upsertSentence({
        segId: message.segId as string,
        seq: Number(message.seq),
        text: (message.text as string) ?? '',
        state: 'revised',
        polishMs: message.polishMs as number | undefined,
      })
      return
    }
    if (type === 'segment.ack') {
      const segId = message.segId as string
      const ok = Boolean(message.ok)
      setSentences((prev) => prev.map((item) => (item.segId === segId ? { ...item, acked: ok } : item)))
      return
    }
    if (type === 'segment.abort') {
      const segId = message.segId as string
      setSentences((prev) => prev.filter((item) => item.segId !== segId))
      return
    }
    if (type === 'warning') {
      notifyBad((message.message as string) ?? '提示')
      return
    }
    if (type === 'error') {
      setError((message.message as string) ?? '识别服务出错')
      setRecording(false)
      recordingRef.current = false
      return
    }
    if (type === 'session.done') {
      sessionRef.current = null
      return
    }
  }, [])

  // 让 WS 回调始终调用最新的 handleMessage，避免闭包过期
  handleMessageRef.current = handleMessage

  function upsertSentence(next: Sentence) {
    setSentences((prev) => {
      const index = prev.findIndex((item) => item.segId === next.segId)
      if (index === -1) {
        return [...prev, next].slice(-MAX_SENTENCES)
      }
      const copy = [...prev]
      copy[index] = { ...copy[index], ...next }
      return copy
    })
  }

  // ---------- 生命周期 ----------

  useEffect(() => {
    if (!token) {
      navigate('/voice/join', { replace: true })
      return
    }
    closedByUsRef.current = false
    connect()
    return () => {
      closedByUsRef.current = true
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current)
      if (heartbeatRef.current) window.clearInterval(heartbeatRef.current)
      if (timerRef.current) window.clearInterval(timerRef.current)
      socketRef.current?.close()
      recorderRef.current?.dispose()
      recorderRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, connect])

  useEffect(() => {
    const node = listRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [sentences])

  // 回到前台时恢复被挂起的音频上下文
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState === 'visible' && recordingRef.current) {
        notifyBad('页面曾切到后台，如果不再出字请重新按住说话')
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  // ---------- 录音控制 ----------

  function startTimer() {
    const startedAt = Date.now()
    setElapsed(0)
    if (timerRef.current) window.clearInterval(timerRef.current)
    timerRef.current = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000)
      setElapsed(seconds)
      if (seconds >= maxSeconds) stopRecording(false)
    }, 200)
  }

  function stopTimer() {
    if (timerRef.current) window.clearInterval(timerRef.current)
    timerRef.current = null
    setLevel(0)
  }

  async function beginRecording() {
    if (recordingRef.current || connection !== 'online') return
    setError('')
    cancelRef.current = false
    setCancelling(false)
    try {
      if (!recorderRef.current) {
        recorderRef.current = createVoiceRecorder({
          sampleRate: 16000,
          frameMs: 100,
          onFrame: (frame) => {
            const socket = socketRef.current
            if (socket && socket.readyState === WebSocket.OPEN) socket.send(frame)
          },
          onLevel: (value) => setLevel(value),
          onError: (err) => setError(err.message),
        })
      }
      await recorderRef.current.prepare()
      await recorderRef.current.start()
      recordingRef.current = true
      setRecording(true)
      startTimer()
      send({ type: 'start', recordingId: `rec_${Date.now().toString(36)}` })
    } catch (caught) {
      recordingRef.current = false
      setRecording(false)
      stopTimer()
      const message = caught instanceof Error ? caught.message : '无法开始录音'
      setError(message)
      notifyBad(message)
    }
  }

  function stopRecording(discard: boolean) {
    if (!recordingRef.current) return
    recordingRef.current = false
    setRecording(false)
    stopTimer()
    recorderRef.current?.stop()
    send({ type: discard ? 'discard' : 'stop' })
    sessionRef.current = null
    cancelRef.current = false
    setCancelling(false)
  }

  function handlePointerDown(event: React.PointerEvent<HTMLButtonElement>) {
    if (connection !== 'online') {
      notifyBad('还没连上服务器，请稍候')
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    startYRef.current = event.clientY
    void beginRecording()
  }

  function handlePointerMove(event: React.PointerEvent<HTMLButtonElement>) {
    if (!recordingRef.current) return
    const moved = startYRef.current - event.clientY
    const next = moved > 60
    if (next !== cancelRef.current) {
      cancelRef.current = next
      setCancelling(next)
    }
  }

  function handlePointerUp() {
    if (!recordingRef.current) return
    // 用 ref 而不是 state：setState 是异步的，抬手瞬间读到的可能是旧值
    stopRecording(cancelRef.current)
  }

  function handlePointerCancel() {
    cancelRef.current = true
    stopRecording(true)
  }

  // ---------- 工具按钮 ----------

  function copyAll() {
    const text = sentences
      .filter((item) => item.state !== 'partial')
      .map((item) => item.text)
      .join('')
    if (!text) {
      notifyBad('还没有可复制的内容')
      return
    }
    copyText(text)
      .then(() => notifyOk('已复制全部文字'))
      .catch(() => notifyBad('复制失败'))
  }

  async function copyText(text: string) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
    const area = document.createElement('textarea')
    area.value = text
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    document.body.removeChild(area)
  }

  if (!token) return null

  const polished = sentences.filter((item) => item.state === 'revised').length

  return (
    <div className="page-enter flex h-svh flex-col bg-ink">
      {/* 顶部状态条 */}
      <header className="flex items-center gap-2 border-b border-line px-3 py-[max(0.6rem,env(safe-area-inset-top))]">
        <Link
          to="/voice/join"
          className="flex h-9 w-9 items-center justify-center rounded-lg text-mist hover:bg-white/5"
          aria-label="返回"
        >
          <ArrowLeft size={18} />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-paper">{roomName || '语音房'}</div>
          <div className="flex items-center gap-2 text-[11px] text-mist">
            <ConnectionBadge state={connection} />
            <span>电脑端 {roomState.desktops}</span>
            {asrModel ? <span className="truncate">{asrModel.replace(/-realtime.*/, '')}</span> : null}
            {polishMode === 'off' ? <span>未纠错</span> : <span>AI 纠错</span>}
          </div>
        </div>
      </header>

      {/* 实时字幕区 */}
      <div ref={listRef} className="flex-1 overflow-y-auto px-3 py-3">
        {sentences.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Mic size={30} className="text-mist/50" />
            <p className="text-sm text-mist">
              {connection === 'online' ? '按住下面的按钮开始说话' : '正在连接房间…'}
            </p>
            {connection === 'online' && roomState.desktops === 0 && (
              <p className="text-xs text-warn">电脑端还没连上，文字会先记在服务器，电脑连上后补发</p>
            )}
          </div>
        ) : (
          <div className="space-y-2 pb-2">
            {sentences.map((item) => (
              <SentenceBubble key={item.segId} sentence={item} />
            ))}
          </div>
        )}
      </div>

      {/* 底部录音区 */}
      <div className="border-t border-line px-3 pb-[max(0.9rem,env(safe-area-inset-bottom))] pt-3">
        {error && <p className="mb-2 text-center text-xs text-danger">{error}</p>}
        <div className="mb-2 flex items-center justify-between text-[11px] text-mist">
          <span>
            {recording ? `正在录音 ${elapsed}s / ${maxSeconds}s` : `${sentences.length} 句 · ${polished} 句已纠错`}
          </span>
          <div className="flex items-center gap-3">
            <button type="button" className="hover:text-paper" onClick={copyAll}>
              <Copy size={13} className="mr-1 inline" />
              复制
            </button>
            <button
              type="button"
              className="hover:text-paper"
              onClick={() => {
                setSentences([])
                notifyOk('已清空本页文字')
              }}
            >
              <Trash2 size={13} className="mr-1 inline" />
              清空
            </button>
          </div>
        </div>

        {/* 音量条 */}
        <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
          <div
            className={cn('h-full rounded-full transition-[width] duration-100', recording ? 'bg-signal' : 'bg-transparent')}
            style={{ width: `${Math.min(100, level * 320)}%` }}
          />
        </div>

        <button
          type="button"
          disabled={connection !== 'online'}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onContextMenu={(event) => event.preventDefault()}
          className={cn(
            'relative flex h-24 w-full select-none touch-none items-center justify-center gap-3 rounded-2xl border text-lg font-medium transition',
            'disabled:opacity-40',
            recording
              ? cancelling
                ? 'border-danger/60 bg-danger/20 text-danger'
                : 'border-signal bg-signal/20 text-signal'
              : 'border-line bg-panel text-paper active:bg-panel-2',
          )}
          style={{ WebkitTouchCallout: 'none' }}
        >
          {recording ? (
            cancelling ? (
              <>
                <MicOff size={26} />
                松开取消
              </>
            ) : (
              <>
                <span className="absolute left-5 h-3 w-3 animate-pulse rounded-full bg-danger" />
                <Mic size={26} />
                松开发送
              </>
            )
          ) : (
            <>
              <Mic size={26} />
              按住说话
            </>
          )}
        </button>
        <p className="mt-2 text-center text-[11px] text-mist">
          {recording ? '上滑可取消本次录音' : '按住不放开始说话，松开自动上屏'}
        </p>
      </div>
    </div>
  )
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  if (state === 'online') {
    return (
      <span className="flex items-center gap-1 text-ok">
        <Wifi size={11} /> 已连接
      </span>
    )
  }
  if (state === 'connecting') {
    return (
      <span className="flex items-center gap-1 text-warn">
        <Loader2 size={11} className="animate-spin" /> 连接中
      </span>
    )
  }
  return (
    <span className="flex items-center gap-1 text-danger">
      <WifiOff size={11} /> 已断开
    </span>
  )
}

function SentenceBubble({ sentence }: { sentence: Sentence }) {
  return (
    <div
      className={cn(
        'rounded-xl border px-3 py-2 text-[15px] leading-relaxed transition-colors duration-300',
        sentence.state === 'partial'
          ? 'border-line/60 bg-panel/40 text-mist'
          : 'border-line bg-panel/90 text-paper',
      )}
    >
      <span>{sentence.text}</span>
      {sentence.state === 'partial' && <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-signal align-middle" />}
      <div className="mt-1 flex items-center gap-2 text-[10px] text-mist">
        {sentence.state === 'revised' && (
          <span className="flex items-center gap-0.5 text-signal">
            <Sparkles size={10} /> AI 纠错
            {sentence.polishMs ? ` ${sentence.polishMs}ms` : ''}
          </span>
        )}
        {sentence.acked && (
          <span className="flex items-center gap-0.5 text-ok">
            <Check size={10} /> 已上屏
          </span>
        )}
      </div>
    </div>
  )
}
