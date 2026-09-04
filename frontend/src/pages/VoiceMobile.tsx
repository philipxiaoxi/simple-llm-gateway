import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Mic, MicOff, RotateCcw, X } from 'lucide-react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'

const MAX_DURATION_MS = 60000
const TARGET_SAMPLE_RATE = 16000

const WORKLET_CODE = `
class PCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / ${TARGET_SAMPLE_RATE};
    this.acc = 0;
    this.chunk = new Int16Array(${TARGET_SAMPLE_RATE / 10});
    this.fill = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const ch = input[0];
      for (let i = 0; i < ch.length; i++) {
        this.acc += 1;
        if (this.acc >= this.ratio) {
          this.acc -= this.ratio;
          const s = ch[i];
          const v = s < 0 ? Math.max(-1, s) * 0x8000 : Math.min(1, s) * 0x7fff;
          this.chunk[this.fill++] = v | 0;
          if (this.fill >= this.chunk.length) {
            const out = this.chunk.slice(0, this.fill);
            this.port.postMessage(out.buffer, [out.buffer]);
            this.fill = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
`

export function VoiceMobilePage() {
  const [searchParams] = useSearchParams()
  const initialCode = (searchParams.get('room') || '').trim().toUpperCase()

  const [room, setRoom] = useState(initialCode)
  const [roomName, setRoomName] = useState('')
  const [joined, setJoined] = useState(Boolean(initialCode))
  const [joining, setJoining] = useState(false)
  const [error, setError] = useState('')

  const [recording, setRecording] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [sentences, setSentences] = useState<string[]>([])
  const [finalResult, setFinalResult] = useState<{ raw_text: string; text: string; delivered: number } | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [micSupported, setMicSupported] = useState(true)

  const wsRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const workletNodeRef = useRef<AudioWorkletNode | null>(null)
  const timerRef = useRef<number | null>(null)
  const startedAtRef = useRef(0)
  const joinedRoomRef = useRef('')
  const recordingRef = useRef(false)
  const sentAnyRef = useRef(false)

  useEffect(() => {
    const supported = Boolean(navigator.mediaDevices?.getUserMedia)
    setMicSupported(supported)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
      cleanupAudio()
      closeWs()
    }
  }, [])

  function closeWs() {
    const ws = wsRef.current
    if (ws) {
      ws.onmessage = null
      ws.onopen = null
      ws.onerror = null
      ws.onclose = null
      try {
        ws.close()
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
  }

  function cleanupAudio() {
    try {
      workletNodeRef.current?.disconnect()
    } catch {
      /* ignore */
    }
    workletNodeRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (audioContextRef.current) {
      void audioContextRef.current.close().catch(() => {})
      audioContextRef.current = null
    }
  }

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

  function connectWs(code: string): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(api.voiceAsrUrl(code))
      ws.binaryType = 'arraybuffer'
      ws.onopen = () => resolve(ws)
      ws.onerror = () => reject(new Error('无法连接服务器'))
      ws.onmessage = (event) => {
        let frame: { type: string; text?: string; raw_text?: string; delivered?: number; message?: string }
        try {
          frame = JSON.parse(String(event.data))
        } catch {
          return
        }
        if (frame.type === 'partial') {
          setLiveText(frame.text ?? '')
        } else if (frame.type === 'sentence') {
          setSentences((prev) => [...prev, frame.text ?? ''])
          setLiveText('')
        } else if (frame.type === 'optimized') {
          setFinalResult({
            raw_text: frame.raw_text ?? sentencesRef.current.join(''),
            text: frame.text ?? '',
            delivered: frame.delivered ?? 0,
          })
          setProcessing(false)
        } else if (frame.type === 'error') {
          setError(frame.message ?? '识别失败')
          setProcessing(false)
        }
      }
      ws.onclose = () => {
        wsRef.current = null
      }
      wsRef.current = ws
    })
  }

  const sentencesRef = useRef<string[]>([])
  useEffect(() => {
    sentencesRef.current = sentences
  }, [sentences])

  async function startRecording() {
    if (!joined || recordingRef.current) return
    recordingRef.current = true
    setRecording(true)
    setProcessing(false)
    setError('')
    setLiveText('')
    setSentences([])
    setFinalResult(null)
    sentAnyRef.current = false

    try {
      const [stream, ws] = await Promise.all([
        navigator.mediaDevices.getUserMedia({ audio: true }),
        connectWs(joinedRoomRef.current),
      ])
      if (!recordingRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        closeWs()
        return
      }
      streamRef.current = stream

      const audioContext = new AudioContext()
      audioContextRef.current = audioContext
      const workletUrl = URL.createObjectURL(new Blob([WORKLET_CODE], { type: 'application/javascript' }))
      await audioContext.audioWorklet.addModule(workletUrl)
      URL.revokeObjectURL(workletUrl)

      const source = audioContext.createMediaStreamSource(stream)
      const workletNode = new AudioWorkletNode(audioContext, 'pcm-recorder')
      workletNode.port.onmessage = (event) => {
        const data = event.data as ArrayBuffer
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(data)
          sentAnyRef.current = true
        }
      }
      source.connect(workletNode)
      workletNodeRef.current = workletNode

      startedAtRef.current = Date.now()
      setElapsed(0)
      timerRef.current = window.setInterval(() => {
        const next = Date.now() - startedAtRef.current
        setElapsed(next)
        if (next >= MAX_DURATION_MS) stopRecording()
      }, 200)
    } catch (caught) {
      recordingRef.current = false
      setRecording(false)
      cleanupAudio()
      closeWs()
      setError(caught instanceof Error ? caught.message : '无法访问麦克风，请检查浏览器权限')
    }
  }

  function stopRecording() {
    if (!recordingRef.current) return
    recordingRef.current = false
    setRecording(false)
    if (timerRef.current) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    const ws = wsRef.current
    cleanupAudio()
    if (ws && ws.readyState === WebSocket.OPEN && sentAnyRef.current) {
      ws.send(JSON.stringify({ type: 'stop' }))
      setProcessing(true)
    } else {
      closeWs()
      setProcessing(false)
    }
  }

  function leave() {
    closeWs()
    cleanupAudio()
    setJoined(false)
    setRoomName('')
    resetResult()
    setRoom('')
    setError('')
  }

  function resetResult() {
    setFinalResult(null)
    setSentences([])
    setLiveText('')
  }

  function formatDuration(ms: number) {
    const seconds = Math.floor(ms / 1000)
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  }

  const liveFullText = sentences.join('') + liveText

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

            {finalResult ? (
              <div className="w-full max-w-md space-y-3">
                <div className="rounded-xl border border-line bg-panel/90 p-4">
                  <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">语音识别</div>
                  <div className="break-words text-paper">{finalResult.raw_text}</div>
                </div>
                <div className="rounded-xl border border-signal/30 bg-signal/5 p-4">
                  <div className="mb-1 text-xs uppercase tracking-[0.16em] text-signal">优化表达</div>
                  <div className="break-words text-paper">{finalResult.text}</div>
                </div>
                <div className="text-center text-xs text-mist">
                  {finalResult.delivered > 0 ? `已推送到 ${finalResult.delivered} 个桌面端` : '桌面端未连接，文本已保存在房间记录'}
                </div>
                <button
                  type="button"
                  onClick={() => resetResult()}
                  className="inline-flex min-h-10 w-full items-center justify-center rounded-md border border-line bg-panel-2 text-sm text-paper hover:border-mist/40"
                >
                  再说一次
                </button>
              </div>
            ) : recording || processing ? (
              <div className="w-full max-w-md space-y-3">
                <div className="rounded-xl border border-line bg-panel/90 p-4">
                  <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">
                    {processing ? '正在优化表达…' : '实时识别（边说边出字）'}
                  </div>
                  <div className="min-h-[1.5rem] break-words text-paper">
                    {liveFullText || (processing ? '…' : '请开始说话')}
                  </div>
                </div>
                <div className="flex items-center justify-center gap-2 text-xs text-mist">
                  {processing ? (
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-signal/30 border-t-signal" />
                  ) : (
                    <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-danger" />
                  )}
                  {processing ? '识别完成，正在用大模型优化' : formatDuration(elapsed)}
                </div>
              </div>
            ) : null}

            {!processing && !finalResult ? (
              <button
                type="button"
                disabled={!micSupported}
                onPointerDown={() => {
                  if (!recording) void startRecording()
                }}
                onPointerUp={() => {
                  if (recording) stopRecording()
                }}
                onPointerLeave={() => {
                  if (recording) stopRecording()
                }}
                onPointerCancel={() => {
                  if (recording) stopRecording()
                }}
                onContextMenu={(event) => event.preventDefault()}
                className={cn(
                  'relative flex h-44 w-44 shrink-0 touch-none select-none items-center justify-center rounded-full border transition-all duration-150 select-none',
                  recording
                    ? 'border-danger bg-danger/15 text-danger'
                    : 'border-signal/40 bg-signal/10 text-signal active:scale-95',
                  !micSupported && 'opacity-40',
                )}
                style={{ touchAction: 'none', WebkitUserSelect: 'none', userSelect: 'none' }}
              >
                <div className="flex flex-col items-center gap-2">
                  {recording ? <MicOff size={40} /> : <Mic size={40} />}
                  <div className="text-sm font-medium">{recording ? '松开结束' : '按住说话'}</div>
                </div>
                {recording ? <span className="absolute inset-0 -z-10 animate-ping rounded-full bg-danger/10" /> : null}
              </button>
            ) : null}

            {!micSupported ? (
              <div className="w-full max-w-md text-center text-sm text-danger">
                当前浏览器不支持录音（需要 HTTPS 环境与 AudioContext）。请用手机浏览器打开此页面。
              </div>
            ) : null}
            {error && !recording ? <div className="w-full max-w-md break-words text-center text-sm text-danger">{error}</div> : null}
          </div>
        </div>
      )}
    </div>
  )
}
