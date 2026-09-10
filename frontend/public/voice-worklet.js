/**
 * 麦克风 PCM 采集 AudioWorkletProcessor。
 *
 * 作为静态文件放在 public 目录（不经打包），主线程通过
 * `audioWorklet.addModule('/voice-worklet.js')` 加载后按名字 'pcm-capture' 实例化。
 *
 * 职责只有一件：把输入通道的 Float32 样本按定长攒帧，postMessage 给主线程。
 * 重采样（48k → 16k）、Float32→Int16、100ms 分帧都在主线程做，这里只负责搬运。
 */

/** 默认帧长：1600 样本 = 100ms @16kHz（见 voiceAudio.ts 的 frameSamples 配置） */
const DEFAULT_FRAME_SAMPLES = 1600
/** 帧长合法性边界：至少 1 个样本，至多 192000（4 秒 @48k），避免异常配置把音频线程撑爆 */
const MIN_FRAME_SAMPLES = 1
const MAX_FRAME_SAMPLES = 192000

/**
 * 校验并归一化帧长。
 * @param {unknown} value
 * @returns {number | null} 合法时返回整数样本数，否则返回 null
 */
function normalizeFrameSamples(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const samples = Math.floor(value)
  if (samples < MIN_FRAME_SAMPLES || samples > MAX_FRAME_SAMPLES) return null
  return samples
}

class PcmCaptureProcessor extends AudioWorkletProcessor {
  /** @param {AudioWorkletNodeOptions} [options] */
  constructor(options) {
    super()
    const configured = normalizeFrameSamples(options?.processorOptions?.frameSamples)
    this.frameSamples = configured ?? DEFAULT_FRAME_SAMPLES
    /** 攒帧缓冲：攒满 frameSamples 就转移给主线程 */
    this.pending = new Float32Array(this.frameSamples)
    this.pendingLength = 0
    this.port.onmessage = (event) => this.handleMessage(event.data)
  }

  /**
   * 处理主线程下发的配置消息。
   * 目前只支持 { type: 'config', frameSamples }。
   * @param {unknown} message
   */
  handleMessage(message) {
    if (!message || message.type !== 'config') return
    const next = normalizeFrameSamples(message.frameSamples)
    if (next === null || next === this.frameSamples) return
    // 重新配置时保留没攒满的样本，避免丢音（新帧长更短时只保留能装下的部分）
    const kept = Math.min(this.pendingLength, next)
    const buffer = new Float32Array(next)
    buffer.set(this.pending.subarray(0, kept))
    this.pending = buffer
    this.pendingLength = kept
    this.frameSamples = next
  }

  /**
   * 音频线程回调，每个渲染量子调用一次（通常 128 帧）。
   * @param {Float32Array[][]} inputs
   * @param {Float32Array[][]} outputs
   * @returns {boolean} 必须返回 true，否则 processor 会被回收
   */
  process(inputs, outputs) {
    const channels = inputs[0]
    // 没接麦克风 / 静音 / 浏览器给了空数组时安全跳过，不能抛错
    const channel = channels && channels.length > 0 ? channels[0] : null
    if (channel && channel.length > 0) {
      // 只取第 0 通道：主线程已用 channelCount:1 + getUserMedia 单声道约束把输入降成单声道
      let pending = this.pending
      let written = this.pendingLength
      const frameSamples = this.frameSamples
      for (let i = 0; i < channel.length; i++) {
        pending[written++] = channel[i]
        if (written === frameSamples) {
          // 转移 buffer 所有权，省掉一次结构化克隆拷贝；随后另开一块继续攒
          this.port.postMessage(pending, [pending.buffer])
          pending = new Float32Array(frameSamples)
          written = 0
        }
      }
      this.pending = pending
      this.pendingLength = written
    }
    // 输出静音：采集链路不需要出声，即使被直连到 destination 也不会把麦克风声音回放出去
    for (let i = 0; i < outputs.length; i++) {
      const output = outputs[i]
      for (let j = 0; j < output.length; j++) output[j].fill(0)
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
