/**
 * segments.js —— 段落账本与「共同前缀裁剪的尾部替换」算法（纯函数，可单测）
 *
 * 设计依据：docs/voice-input/桌面客户端.md §4、技术方案.md §3.4~3.6
 *
 * 核心不变量：
 *   1. 服务端永远是「整段快照」，本地账本记录「我认为已经注入到输入框里的文本」。
 *   2. 只有**位于光标之前、且是我们注入的那一段**（tailSegId）才允许回改；
 *      一旦有新段落接在后面，前一段就无法再回改（尾部替换算法的物理限制）。
 *   3. 一切字符计算都按 Unicode 字符数组（Array.from）进行，emoji / 代理对不会被拆开。
 */

/** 放弃替换的原因枚举（协议参考.md §2.4） */
export const ABANDON_REASONS = Object.freeze([
  'cursor_mismatch',
  'user_typed',
  'timeout',
  'mode_off',
  'disabled',
  'room_busy',
])

/** 注入失败原因枚举（协议参考.md §2.4） */
export const INJECT_ERROR_REASONS = Object.freeze([
  'no_focus',
  'perm_denied',
  'clipboard_failed',
  'secure_input',
])

/**
 * rev 语义（协议参考.md §2.4）：**rev 是状态档位，不是自增版本号**
 *   0 = ASR 中间结果（partial，final=false，可自由替换，同一段会下发多次）
 *   1 = ASR 终稿（final 字段仍为 false，可替换，等纠错）
 *   2 = 纠错终稿（final=true）→ 客户端把该段标记为 committed，之后只允许正向追加
 *
 * ⚠️ 重要：后端实测（backend/app/services/voice_session.py `_on_partial`）会对同一段
 * 反复下发 rev=0 的快照（≥120ms 一次，文本不断增长）。因此去重规则必须是
 * 「rev 更旧才丢」+「文本无变化才算重复」，**不能**用 `rev <= prev.rev` 判定幂等，
 * 否则一句话只会显示第一个中间结果。
 */
export const REV_PARTIAL = 0
export const REV_FINAL = 1
export const REV_REVISED = 2

/**
 * 允许「延后重试」而不是立刻放弃的闸门。
 * 依据：技术方案.md §3.6「停顿窗口」——距上次上屏 <600ms 或房间正在录音时应当
 * **推迟**（最多 3s / 等到停止录音），而不是永久放弃替换；否则一句话里每次
 * ASR 修正标点都会把整段锁死，润色终稿就再也替换不回去了。
 */
export const DEFERRED_GATES = Object.freeze(['room_busy', 'too_soon'])

/**
 * 延后重试上限（毫秒）。
 * - `too_soon`：文档 §3.6 明确「最多推迟 3s，超时则放弃」
 * - `room_busy`：等到录音停止为止。房间最长录音 120s（maxRecordingSeconds），
 *   所以按 120s 兜底；期间只做安全的正向追加，停止录音后一次性原子替换。
 */
export const DEFER_MAX_MS = Object.freeze({ too_soon: 3000, room_busy: 120000 })

/** Unicode 字符数（emoji / 代理对按 1 个字符算） */
export function charLen(text) {
  return Array.from(String(text ?? '')).length
}

/** Unicode 字符级公共前缀长度（正确处理 emoji / 代理对） */
export function commonPrefixLen(a, b) {
  const A = Array.from(String(a ?? ''))
  const B = Array.from(String(b ?? ''))
  const n = Math.min(A.length, B.length)
  let i = 0
  while (i < n && A[i] === B[i]) i += 1
  return i
}

/**
 * 计算把已注入的 prev 变成 next 所需的最小尾部操作。
 * @returns {{prefix:string, tailOld:number, tailNew:string, prevChars:number, nextChars:number}}
 */
export function diffTail(prev, next) {
  const A = Array.from(String(prev ?? ''))
  const B = Array.from(String(next ?? ''))
  const p = commonPrefixLen(A.join(''), B.join(''))
  return {
    prefix: A.slice(0, p).join(''),
    tailOld: A.length - p,
    tailNew: B.slice(p).join(''),
    prevChars: A.length,
    nextChars: B.length,
  }
}

/**
 * 【纯函数】三道闸判定：返回 null 表示允许替换，否则返回闸门原因。
 *
 * 与文档 §4.1 一致，另外补了一条工程上必需的检查：`isTail`。
 * 只有当该段仍然紧贴光标（是我们注入的最后一段）时，尾部替换才有意义。
 *
 * @param {object} s
 * @param {boolean} s.replaceEnabled 配置开关
 * @param {boolean} s.insertMode 是否处于插入模式
 * @param {boolean} s.roomBusy 房间内是否还有人在录音
 * @param {number}  s.msSinceLastInject 距上次注入的毫秒数
 * @param {number}  s.commitDelayMs 停顿窗口
 * @param {boolean} s.isTail 该段是否仍在光标处（未被后续段落覆盖）
 * @param {boolean} s.modeDirty 是否已放弃替换权（用户可能手输）
 * @param {number|null} s.expectedPos 期望光标位置（本会话注入字符总数）
 * @param {number}  s.actualCursorPos 实际光标位置，-1 表示取不到 → 跳过检查
 * @returns {null|'disabled'|'mode_off'|'room_busy'|'too_soon'|'cursor_mismatch'}
 */
export function canReplace({
  replaceEnabled = true,
  insertMode = true,
  roomBusy = false,
  msSinceLastInject = Number.POSITIVE_INFINITY,
  commitDelayMs = 600,
  isTail = true,
  modeDirty = false,
  expectedPos = null,
  actualCursorPos = -1,
} = {}) {
  if (!replaceEnabled) return 'disabled'
  if (!insertMode) return 'mode_off'
  if (!isTail) return 'cursor_mismatch'
  if (roomBusy) return 'room_busy'
  if (msSinceLastInject < commitDelayMs) return 'too_soon'
  if (!modeDirty && expectedPos !== null && actualCursorPos !== -1 && actualCursorPos !== expectedPos) {
    return 'cursor_mismatch'
  }
  return null
}

/**
 * 闸门 → 处理方式。
 * @returns {'go'|'defer'|'abandon'}
 */
export function gateAction(reason) {
  if (reason === null || reason === undefined) return 'go'
  return DEFERRED_GATES.includes(reason) ? 'defer' : 'abandon'
}

/** 把动作渲染成中文短句，用于日志 */
export function describePlan(plan) {
  if (!plan) return '无操作'
  switch (plan.kind) {
    case 'insert':
      return `插入「${plan.text}」`
    case 'append':
      return `追加「${plan.tailNew}」`
    case 'replace':
      return `替换尾部「${plan.tailNew}」`
    case 'delete':
      return `回删 ${plan.tailOld} 字`
    case 'skip':
      return `跳过（${plan.reason}）`
    default:
      return plan.kind
  }
}

/**
 * 段落账本。
 *
 * 每条账：{ segId, text, rev, final, committed, injected, chars, updatedAt }
 *  - text     ：服务端该段最新快照（本地认为已生效的文本）
 *  - injected ：文本是否真的进了输入框（插入模式关闭时不进）
 *  - committed：已落定，不再回改，只允许正向追加
 */
export class SegmentLedger {
  constructor({ maxSegments = 500, now = () => Date.now() } = {}) {
    /** @type {Map<string, object>} */
    this.map = new Map()
    this.maxSegments = maxSegments
    this.now = now
    /** 期望光标位置 = 本会话注入的 Unicode 字符总数；null 表示未知（跳过检查） */
    this.expectedPos = 0
    /** 用户可能手动输入过 → 放弃替换权 */
    this.modeDirty = false
    /** 当前紧贴光标的段落（只有它能被回改） */
    this.tailSegId = null
  }

  get size() {
    return this.map.size
  }

  get(segId) {
    return this.map.get(segId) || null
  }

  has(segId) {
    return this.map.has(segId)
  }

  /** 该段是否仍紧贴光标（可以被安全回改） */
  isTail(segId) {
    return this.tailSegId === null || this.tailSegId === segId
  }

  /**
   * 规划一次快照的处理动作（不修改账本，调用方执行成功后调用 commit/markCommitted）。
   * @param {{segId:string, rev:number, text:string, final?:boolean, seq?:number}} msg
   * @returns {null|object} null 表示无需任何操作（幂等或文本未变）
   */
  plan({ segId, rev = 0, text = '', final = false, seq = 0 }) {
    const body = String(text ?? '')
    const prev = this.map.get(segId)
    const isTail = this.isTail(segId)

    // 1) 新段落（或此前因插入模式关闭而只记账、从未真正注入）→ 整体插入
    if (!prev || !prev.injected) {
      return {
        kind: 'insert',
        segId,
        seq,
        rev,
        final: Boolean(final),
        text: body,
        chars: charLen(body),
        tailOld: 0,
        tailNew: body,
        prefix: '',
        isTail: true,
        prevText: '',
      }
    }

    // 2) 幂等：只丢弃**更旧**的版本。rev 是状态档位（0/1/2），同一档位会下发多次，
    //    文本可能增长（ASR 中间结果），所以必须继续处理，靠下面的 diffTail 判重。
    if (rev < prev.rev) return null

    const d = diffTail(prev.text, body)
    const base = {
      segId,
      seq,
      rev,
      final: Boolean(final),
      text: body,
      chars: d.nextChars,
      tailOld: d.tailOld,
      tailNew: d.tailNew,
      prefix: d.prefix,
      isTail,
      prevText: prev.text,
      prevRev: prev.rev,
    }

    // 3) 文本没变化 → 无操作（只更新 rev，由 commit 负责）
    if (d.tailOld === 0 && d.tailNew === '') return { ...base, kind: 'skip', reason: 'unchanged' }

    // 4) 已落定的段落：只允许正向追加，绝不回改
    if (prev.committed && d.tailOld > 0) {
      if (d.tailNew === '') return { ...base, kind: 'skip', reason: 'committed' }
      // 只把「新增的尾巴」贴到光标处：tailOld 归零，不做任何回删/选中
      return { ...base, kind: 'append', tailOld: 0, appendOnly: true, blocked: 'committed' }
    }

    // 5) 共同前缀之后没有旧内容 → 纯追加（永远安全，不需要过闸门）
    if (d.tailOld === 0) return { ...base, kind: 'append', appendOnly: false }

    // 6) 新尾部为空 → 纯回删
    if (d.tailNew === '') return { ...base, kind: 'delete' }

    // 7) 常规替换
    return { ...base, kind: 'replace' }
  }

  /**
   * 记录某段已按快照生效。
   * @param {{segId:string, rev:number, text:string, final?:boolean, injected?:boolean}} msg
   */
  commit({ segId, rev, text, final = false, injected = true }) {
    const prev = this.map.get(segId)
    const committed = Boolean(final) || Boolean(prev?.committed)
    this.map.set(segId, {
      segId,
      text: String(text ?? ''),
      rev,
      final: Boolean(final),
      committed,
      injected: prev ? prev.injected || injected : injected,
      chars: charLen(text),
      updatedAt: this.now(),
    })
    this.prune()
    return this.map.get(segId)
  }

  /** 只更新 rev/text（例如收到无需动作的快照） */
  touch(segId, { rev, text, final = false }) {
    const prev = this.map.get(segId)
    if (!prev) return null
    prev.rev = rev
    prev.text = String(text ?? prev.text)
    prev.final = Boolean(final)
    if (final) prev.committed = true
    prev.chars = charLen(prev.text)
    prev.updatedAt = this.now()
    return prev
  }

  /**
   * 把某段标记为已落定（不再回改）。
   * @returns {object|null} 被改动的账目
   */
  markCommitted(segId) {
    const prev = this.map.get(segId)
    if (!prev) return null
    prev.committed = true
    return prev
  }

  /** 记录「未注入」的段落（插入模式关闭时） */
  markPending({ segId, rev, text, final = false, seq = 0 }) {
    this.map.set(segId, {
      segId,
      seq,
      text: String(text ?? ''),
      rev,
      final: Boolean(final),
      committed: Boolean(final),
      injected: false,
      chars: charLen(text),
      updatedAt: this.now(),
    })
    this.prune()
    return this.map.get(segId)
  }

  /**
   * 记录一段「出生前就已经上屏」的历史文本（重连补发 / 首次连接的历史段落）。
   * 只登记、不注入，也不影响 expectedPos。
   */
  markHistory({ segId, rev = 2, text = '', seq = 0, final = true }) {
    const now = this.now()
    this.map.set(segId, {
      segId,
      seq,
      text: String(text ?? ''),
      rev,
      final: Boolean(final),
      committed: true,
      injected: true, // 关键：不会被当成「待插入」而重新上屏
      history: true,
      chars: charLen(text),
      updatedAt: now,
    })
    this.prune()
    return this.map.get(segId)
  }

  /**
   * 放弃所有未落定段落的替换权（用户手输 / 焦点窗口变化 / 手动清空）。
   * @returns {Array<{segId:string, reason:string}>}
   */
  abandonAll(reason = 'user_typed') {
    const abandoned = []
    for (const seg of this.map.values()) {
      if (!seg.committed) {
        seg.committed = true
        abandoned.push({ segId: seg.segId, reason })
      }
    }
    if (abandoned.length > 0) this.modeDirty = true
    return abandoned
  }

  /**
   * 有新段落插到光标处：其余段落不再紧贴光标，永久失去回改资格。
   * @returns {string[]} 被冻结的 segId 列表
   */
  freezeOthers(keepSegId) {
    const frozen = []
    for (const seg of this.map.values()) {
      if (seg.segId === keepSegId) continue
      if (!seg.committed) {
        seg.committed = true
        frozen.push(seg.segId)
      }
    }
    return frozen
  }

  /** 段落成为新的尾部 */
  setTail(segId) {
    this.tailSegId = segId
  }

  /** 段落被撤销/删除 */
  forget(segId) {
    const prev = this.map.get(segId)
    this.map.delete(segId)
    if (this.tailSegId === segId) this.tailSegId = null
    return prev
  }

  /** 更新期望光标位置（delta 为本次实际写入输入框的字符数，可为负） */
  noteInjected(deltaChars) {
    if (this.expectedPos === null) return this.expectedPos
    this.expectedPos += deltaChars
    if (this.expectedPos < 0) this.expectedPos = 0
    return this.expectedPos
  }

  /** 光标信息不可信（例如切换了前台 App）→ 之后的检查一律跳过 */
  invalidateCursor() {
    this.expectedPos = null
    this.modeDirty = true
  }

  /**
   * 用实测光标位置重新基线化（整段插入后调用一次）。
   * 好处：即使输入框本来就不为空，位置预期依然准确；同时清掉 modeDirty，
   * 让「光标不符就放弃替换」这道保护重新生效。
   */
  rebaselineCursor(pos, { clearDirty = true } = {}) {
    if (!Number.isFinite(pos) || pos < 0) return this.expectedPos
    this.expectedPos = Math.round(pos)
    if (clearDirty) this.modeDirty = false
    return this.expectedPos
  }

  /** 最近一条尚未注入的段落（切到插入模式时补上） */
  lastPending() {
    let found = null
    for (const seg of this.map.values()) {
      if (seg.injected) continue
      if (!found || seg.updatedAt >= found.updatedAt) found = seg
    }
    return found
  }

  /** 已注入但未落定的段落数 */
  openCount() {
    let n = 0
    for (const seg of this.map.values()) if (seg.injected && !seg.committed) n += 1
    return n
  }

  /** 清空账本（不清 expectedPos 以外的运行状态） */
  clear({ keepCursor = false } = {}) {
    this.map.clear()
    this.tailSegId = null
    if (!keepCursor) {
      this.expectedPos = 0
      this.modeDirty = false
    }
  }

  /** 超量清理：按写入顺序丢弃最旧的账目 */
  prune() {
    if (this.map.size <= this.maxSegments) return
    const overflow = this.map.size - this.maxSegments
    let i = 0
    for (const key of this.map.keys()) {
      if (i >= overflow) break
      if (key === this.tailSegId) continue
      this.map.delete(key)
      i += 1
    }
  }

  /** 状态摘要（`s` 命令与日志） */
  stats() {
    let committed = 0
    let injected = 0
    let pending = 0
    for (const seg of this.map.values()) {
      if (seg.committed) committed += 1
      if (seg.injected) injected += 1
      else pending += 1
    }
    return {
      total: this.map.size,
      injected,
      pending,
      committed,
      open: this.openCount(),
      expectedPos: this.expectedPos,
      tailSegId: this.tailSegId,
      modeDirty: this.modeDirty,
    }
  }
}
