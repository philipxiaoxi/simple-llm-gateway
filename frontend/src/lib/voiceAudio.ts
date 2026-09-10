/**
 * 浏览器端麦克风采集：把麦克风声音转成定长的 16kHz 单声道 PCM16LE 二进制帧。
 *
 * 链路：getUserMedia → AudioContext → AudioWorkletNode('pcm-capture')
 *      → 主线程累积 → 线性插值重采样到目标采样率 → Float32→Int16 → 定长帧回调
 *
 * 默认产出：每帧 100ms、16000Hz、单声道、PCM16LE，正好 3200 字节，可直接用
 * WebSocket 二进制帧上传给后端做实时语音识别。
 *
 * AudioWorklet 不可用时（Safari < 14.5 等）自动降级为 ScriptProcessorNode，
 * 两条路径共用同一套重采样 + 分帧代码，产出完全一致。
 */

export type VoiceRecorderOptions = {
  /** 目标采样率，默认 16000 */
  sampleRate?: number
  /** 每帧毫秒数，默认 100 */
  frameMs?: number
  /** 每个 PCM16LE 帧回调（100ms = 3200 字节） */
  onFrame: (frame: ArrayBuffer) => void
  /** 0..1 的实时音量（RMS），用于 UI 波形 */
  onLevel?: (level: number) => void
  onError?: (error: Error) => void
}

export type VoiceRecorder = {
  /** 申请麦克风权限并初始化（必须在用户手势里调用，否则 iOS 会挂起 AudioContext） */
  prepare(): Promise<void>
  /** 开始采集；未 prepare 时内部会自动 prepare */
  start(): Promise<void>
  /** 停止采集（保留 MediaStream，便于快速再次开始） */
  stop(): void
  /** 彻底释放：停止轨道、关闭 AudioContext（页面卸载时调用） */
  dispose(): void
  /** 是否正在采集 */
  readonly recording: boolean
  /** 当前实际使用的采集采样率 */
  readonly inputSampleRate: number
}

const DEFAULT_SAMPLE_RATE = 16000
const DEFAULT_FRAME_MS = 100
/** ScriptProcessorNode 兜底路径的块大小（该 API 只接受 256 的整数倍档位） */
const SCRIPT_PROCESSOR_BUFFER_SIZE = 4096
/** worklet 文件里的 processor 注册名，两边必须一致 */
const WORKLET_PROCESSOR_NAME = 'pcm-capture'
/** dispose() 打断进行中的 prepare() 时抛出的固定文案 */
const DISPOSED_MESSAGE = '录音器已释放'
const EMPTY_FLOAT32 = new Float32Array(0)

/** Float32 → Int16：乘 32767 后软限幅到 [-32768, 32767]（NaN/Infinity 归零） */
function floatToInt16(value: number): number {
  if (!Number.isFinite(value)) return 0
  const scaled = Math.round(value * 32767)
  if (scaled > 32767) return 32767
  if (scaled < -32768) return -32768
  return scaled
}

/**
 * 线性插值重采样器：输入采样率任意，输出目标采样率。
 *
 * 用「累积浮点位置」推进，不假设整数比（48k→16k 是 3:1，44.1k→16k 是 2.75625:1，
 * 两者都走同一条路径）。输入采样率等于目标采样率时插值系数恒为 0，等价于原样拷贝。
 *
 * 状态只有三样：pos（下一个输出样本在输入时间轴上的浮点位置）、buf/bufStart
 * （一小段还没用掉的输入尾巴）。每次 push 会把尾巴和新数据拼起来再消费。
 */
class LinearResampler {
  /** 每个输出样本需要前进的输入样本数（输入采样率 / 输出采样率） */
  private readonly ratio: number
  /** 下一个输出样本在输入时间轴上的浮点位置 */
  private pos = 0
  /** 尚未消费的输入样本（含插值所需的最后一个点） */
  private buf = EMPTY_FLOAT32
  /** buf[0] 在输入时间轴上的整数下标 */
  private bufStart = 0
  /** 复用的拼接缓冲，容量只增不减 */
  private scratch = EMPTY_FLOAT32
  /** 复用的输出缓冲（返回值是它的视图，只在下一次 push 前有效） */
  private out = EMPTY_FLOAT32

  constructor(inputRate: number, outputRate: number) {
    this.ratio = inputRate / outputRate
  }

  /** 重新开始采集时清空时间轴状态，避免上一段的尾巴串到新一段里 */
  reset(): void {
    this.pos = 0
    this.buf = EMPTY_FLOAT32
    this.bufStart = 0
  }

  /**
   * 送入一块输入样本，返回本次能产出的输出样本（可能为空）。
   * 返回的是内部缓冲视图，调用方需在本方法返回后立即消费，不要持有。
   */
  push(input: Float32Array): Float32Array {
    const incoming = input.length
    if (incoming === 0) return EMPTY_FLOAT32

    const tail = this.buf.length
    const work = this.ensureScratch(tail + incoming)
    work.set(this.buf, 0)
    work.set(input, tail)

    const workStart = this.bufStart
    const workEnd = workStart + tail + incoming // 排他上界（输入时间轴坐标）

    // 线性插值需要 floor(pos) 与 floor(pos)+1 两个点都在手上才能算
    const capacity = Math.max(0, Math.ceil((workEnd - 1 - this.pos) / this.ratio)) + 1
    const out = this.ensureOut(capacity)
    let count = 0
    while (Math.floor(this.pos) < workEnd - 1) {
      const base = Math.floor(this.pos)
      const weight = this.pos - base
      const index = base - workStart
      const prev = work[index]
      const next = work[index + 1]
      out[count++] = prev + (next - prev) * weight
      this.pos += this.ratio
    }

    // 只留下从 floor(pos) 开始的一两个样本等下一块补齐，其余丢弃
    const length = tail + incoming
    const keepFrom = Math.min(Math.max(Math.floor(this.pos) - workStart, 0), length)
    this.buf = work.slice(keepFrom, length)
    this.bufStart = workStart + keepFrom
    return out.subarray(0, count)
  }

  private ensureScratch(size: number): Float32Array {
    if (this.scratch.length < size) this.scratch = new Float32Array(Math.max(size, 4096))
    return this.scratch
  }

  private ensureOut(size: number): Float32Array {
    if (this.out.length < size) this.out = new Float32Array(Math.max(size, 1024))
    return this.out
  }
}

type AudioContextCtor = new (options?: AudioContextOptions) => AudioContext

function getAudioContextCtor(): AudioContextCtor | null {
  if (typeof window === 'undefined') return null
  // 老 Safari 只提供 webkitAudioContext；AudioContext 是 DOM 全局变量，不在 Window 接口上，故显式断言
  const scope = window as unknown as {
    AudioContext?: AudioContextCtor
    webkitAudioContext?: AudioContextCtor
  }
  return scope.AudioContext ?? scope.webkitAudioContext ?? null
}

/** publicDir 下的静态文件会被原样复制到站点根目录；用 BASE_URL 兼容部署在子路径的情况 */
function resolveWorkletUrl(): string {
  // 非 Vite 环境（如 Node 单测）里 import.meta.env 不存在，退化为根路径
  const env = import.meta.env as { BASE_URL?: string } | undefined
  const base = env?.BASE_URL ?? '/'
  return `${base.endsWith('/') ? base : `${base}/`}voice-worklet.js`
}

/** 把 getUserMedia 的英文错误名翻译成可展示的中文 Error */
function translateMediaError(error: unknown): Error {
  const name = error instanceof Error ? error.name : ''
  const detail = error instanceof Error && error.message ? `（${error.message}）` : ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return new Error('麦克风权限被拒绝：请在浏览器地址栏的站点设置里允许使用麦克风后重试')
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return new Error('没有检测到麦克风设备：请确认设备已连接麦克风后重试')
    case 'NotReadableError':
    case 'TrackStartError':
      return new Error('麦克风被占用：请关闭其它正在使用麦克风的程序（会议、录音软件）后重试')
    case 'OverconstrainedError':
      return new Error('麦克风不支持所请求的采集参数：请更换设备或浏览器后重试')
    case 'SecurityError':
      return new Error('当前环境不允许使用麦克风：请使用 HTTPS（或 localhost）打开本页面')
    case 'AbortError':
      return new Error('麦克风启动被中断：请重新按住说话')
    default:
      return new Error(`麦克风初始化失败${detail || '：未知错误'}`)
  }
}

function stopStream(stream: MediaStream | null): void {
  if (!stream) return
  for (const track of stream.getTracks()) track.stop()
}

function closeContext(ctx: AudioContext | null): void {
  if (!ctx || ctx.state === 'closed') return
  void ctx.close().catch(() => {
    /* 关闭失败无需处理 */
  })
}

/** 供 UI 判断环境是否可用 */
export function checkVoiceSupport(): { supported: boolean; reason?: string } {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') {
    return { supported: false, reason: '当前不是浏览器环境，无法使用麦克风' }
  }
  if (!window.isSecureContext) {
    return { supported: false, reason: '麦克风只能在安全上下文（HTTPS 或 localhost）下使用，请用 https 打开本页面' }
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return { supported: false, reason: '当前浏览器不支持麦克风采集接口（mediaDevices.getUserMedia）' }
  }
  if (!getAudioContextCtor()) {
    return { supported: false, reason: '当前浏览器不支持 Web Audio API（AudioContext），无法采集音频' }
  }
  return { supported: true }
}

export function createVoiceRecorder(options: VoiceRecorderOptions): VoiceRecorder {
  const targetSampleRate = Math.max(1, Math.round(options.sampleRate ?? DEFAULT_SAMPLE_RATE))
  const frameMs = options.frameMs && options.frameMs > 0 ? options.frameMs : DEFAULT_FRAME_MS
  /** 每帧的输出样本数：100ms @16kHz = 1600 */
  const frameSamples = Math.max(1, Math.round((targetSampleRate * frameMs) / 1000))
  const onFrame = options.onFrame
  const onLevel = options.onLevel

  let audioCtx: AudioContext | null = null
  let stream: MediaStream | null = null
  let sourceNode: MediaStreamAudioSourceNode | null = null
  let workletNode: AudioWorkletNode | null = null
  let scriptNode: ScriptProcessorNode | null = null
  let muteGain: GainNode | null = null
  let resampler: LinearResampler | null = null

  let prepared = false
  let preparing: Promise<void> | null = null
  /** 每次 teardown 自增：用于打断进行中的 prepare()，防止释放后又把资源挂回去 */
  let generation = 0
  let recording = false
  let inputSampleRate = 0
  let visibilityBound = false

  /** 重采样之后的 Int16 攒帧缓冲 */
  const frameBuffer = new Int16Array(frameSamples)
  let frameOffset = 0

  function reportError(error: Error): Error {
    options.onError?.(error)
    return error
  }

  /** 把一帧 PCM16LE 交给调用方（每帧单独分配，避免被后续写入覆盖） */
  function emitFrame(): void {
    const buffer = new ArrayBuffer(frameSamples * 2)
    new Int16Array(buffer).set(frameBuffer)
    onFrame(buffer)
  }

  /** 音频块入口：worklet 与 ScriptProcessorNode 两条路径都走这里 */
  function handleChunk(chunk: Float32Array): void {
    if (!recording || chunk.length === 0) return
    if (onLevel) {
      let sum = 0
      for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i]
      const rms = Math.sqrt(sum / chunk.length)
      // 原始 RMS，UI 想要更明显的波形可自行放大（人声 RMS 通常在 0.05~0.3）
      onLevel(rms > 1 ? 1 : rms)
    }
    const activeResampler = resampler
    if (!activeResampler) return
    const resampled = activeResampler.push(chunk)
    for (let i = 0; i < resampled.length; i++) {
      frameBuffer[frameOffset++] = floatToInt16(resampled[i])
      if (frameOffset < frameSamples) continue
      frameOffset = 0
      emitFrame()
      // 回调里可能调用了 stop()/dispose()，此时剩下的样本直接丢弃
      if (!recording) return
    }
  }

  async function requestMicrophone(): Promise<MediaStream> {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
    } catch (error) {
      throw reportError(translateMediaError(error))
    }
  }

  /**
   * 优先走 AudioWorklet；不可用（旧 Safari / 非安全上下文 / 模块加载失败）时返回 null，
   * 由调用方降级到 ScriptProcessorNode。
   */
  async function createWorkletNode(ctx: AudioContext): Promise<AudioWorkletNode | null> {
    if (typeof AudioWorkletNode === 'undefined' || typeof ctx.audioWorklet?.addModule !== 'function') {
      console.warn('[语音采集] 当前浏览器不支持 AudioWorklet，已降级为 ScriptProcessorNode（兼容模式，延迟略高）')
      return null
    }
    try {
      await ctx.audioWorklet.addModule(resolveWorkletUrl())
      const node = new AudioWorkletNode(ctx, WORKLET_PROCESSOR_NAME, {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        channelCount: 1,
        channelCountMode: 'explicit',
        channelInterpretation: 'speakers',
        processorOptions: { frameSamples },
      })
      node.port.onmessage = (event: MessageEvent) => {
        const data: unknown = event.data
        if (data instanceof Float32Array) handleChunk(data)
      }
      // worklet 只按这个批量搬运输入样本（与输入采样率无关），真正的分帧在重采样之后做
      node.port.postMessage({ type: 'config', frameSamples })
      return node
    } catch (error) {
      console.warn('[语音采集] AudioWorklet 加载失败，已降级为 ScriptProcessorNode（兼容模式，延迟略高）：', error)
      return null
    }
  }

  function createScriptNode(ctx: AudioContext): ScriptProcessorNode {
    const node = ctx.createScriptProcessor(SCRIPT_PROCESSOR_BUFFER_SIZE, 1, 1)
    node.onaudioprocess = (event: AudioProcessingEvent) => {
      handleChunk(event.inputBuffer.getChannelData(0))
    }
    return node
  }

  /** iOS/Android 切后台会挂起 AudioContext，回前台时尝试恢复 */
  function onVisibilityChange(): void {
    if (typeof document === 'undefined' || document.visibilityState !== 'visible') return
    const ctx = audioCtx
    if (!ctx || ctx.state !== 'suspended') return
    console.info('[语音采集] 页面回到前台，AudioContext 处于挂起状态，正在尝试恢复；若无声请松开后重新按住说话')
    void ctx.resume().catch(() => {
      console.warn('[语音采集] AudioContext 恢复失败，请松开后重新按住说话')
    })
  }

  function bindVisibility(): void {
    if (visibilityBound || typeof document === 'undefined') return
    document.addEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = true
  }

  function unbindVisibility(): void {
    if (!visibilityBound || typeof document === 'undefined') return
    document.removeEventListener('visibilitychange', onVisibilityChange)
    visibilityBound = false
  }

  async function doPrepare(): Promise<void> {
    const support = checkVoiceSupport()
    if (!support.supported) throw reportError(new Error(support.reason ?? '当前环境不支持麦克风采集'))

    const myGeneration = generation
    let nextStream: MediaStream | null = null
    let nextCtx: AudioContext | null = null
    try {
      nextStream = await requestMicrophone()
      if (myGeneration !== generation) throw new Error(DISPOSED_MESSAGE)
      const Ctor = getAudioContextCtor()
      if (!Ctor) throw reportError(new Error('当前浏览器不支持 Web Audio API（AudioContext），无法采集音频'))
      nextCtx = new Ctor()
      // iOS Safari 要求 AudioContext 在用户手势里创建并 resume，否则会一直处于 suspended
      await nextCtx.resume()
      if (myGeneration !== generation) throw new Error(DISPOSED_MESSAGE)
    } catch (error) {
      stopStream(nextStream)
      closeContext(nextCtx)
      throw error instanceof Error ? error : new Error(String(error))
    }

    if (!nextStream || !nextCtx) {
      stopStream(nextStream)
      closeContext(nextCtx)
      throw new Error('麦克风初始化失败')
    }

    // 前置条件都满足了，挂到模块状态上（此后由 teardown 统一释放）
    stream = nextStream
    audioCtx = nextCtx
    inputSampleRate = nextCtx.sampleRate
    resampler = new LinearResampler(nextCtx.sampleRate, targetSampleRate)
    frameOffset = 0
    sourceNode = nextCtx.createMediaStreamSource(nextStream)
    // 采集链路不需要出声，用 0 增益节点接 destination：既保证图被驱动，又不会啸叫
    muteGain = nextCtx.createGain()
    muteGain.gain.value = 0
    muteGain.connect(nextCtx.destination)

    const node = await createWorkletNode(nextCtx)
    if (myGeneration !== generation) {
      // prepare 期间被 dispose() 打断：dispose 已经收走了上面挂上的资源，
      // 这里只需清掉刚建出来、还没登记的节点（不能再调 teardown，否则会误杀之后新建的链路）
      if (node) {
        node.port.onmessage = null
        node.disconnect()
      }
      throw new Error(DISPOSED_MESSAGE)
    }
    if (node) {
      workletNode = node
      sourceNode.connect(node)
      node.connect(muteGain)
    } else {
      scriptNode = createScriptNode(nextCtx)
      sourceNode.connect(scriptNode)
      scriptNode.connect(muteGain)
    }
    bindVisibility()
    prepared = true
  }

  async function prepare(): Promise<void> {
    if (prepared) return
    if (!preparing) {
      const task = doPrepare()
      preparing = task
      // 结算后清空缓存（只在没有被更晚的 prepare 顶替时清），失败由调用方通过 task 感知
      const settle = () => {
        if (preparing === task) preparing = null
      }
      task.then(settle, settle)
    }
    return preparing
  }

  async function start(): Promise<void> {
    if (recording) return
    if (!prepared) await prepare()
    const ctx = audioCtx
    const activeResampler = resampler
    if (!ctx || !activeResampler) throw reportError(new Error('录音链路未就绪，请重试'))
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume()
      } catch {
        throw reportError(new Error('音频上下文恢复失败：请重新按住说话'))
      }
    }
    activeResampler.reset()
    frameOffset = 0
    recording = true
  }

  function stop(): void {
    if (!recording) return
    recording = false
    // 丢掉不足一帧的残留，避免和下一段录音的第一帧拼在一起
    frameOffset = 0
  }

  function teardown(): void {
    generation += 1
    recording = false
    frameOffset = 0
    unbindVisibility()
    if (workletNode) {
      workletNode.port.onmessage = null
      workletNode.disconnect()
      workletNode = null
    }
    if (scriptNode) {
      scriptNode.onaudioprocess = null
      scriptNode.disconnect()
      scriptNode = null
    }
    if (sourceNode) {
      sourceNode.disconnect()
      sourceNode = null
    }
    if (muteGain) {
      muteGain.disconnect()
      muteGain = null
    }
    stopStream(stream)
    stream = null
    const ctx = audioCtx
    audioCtx = null
    closeContext(ctx)
    resampler = null
    prepared = false
    preparing = null
    inputSampleRate = 0
  }

  function dispose(): void {
    teardown()
  }

  return {
    prepare,
    start,
    stop,
    dispose,
    get recording() {
      return recording
    },
    get inputSampleRate() {
      return inputSampleRate
    },
  }
}
