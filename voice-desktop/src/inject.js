/**
 * inject.js —— 平台注入实现（v1：macOS）
 *
 * 主方案：`pbcopy` 写剪贴板 + `osascript` 发 Cmd+V（设计依据 桌面客户端.md §3）
 *  - 中文 / emoji 天然支持，跨 App 通用，不触发输入法候选框
 *  - 不使用 shell 拼接，全部走 execFile(cmd, args)，避免转义问题
 *  - 不引入任何原生模块（nut-js / robotjs / node-gyp 一律不用）
 *
 * 所有系统调用都通过依赖注入的 `run(cmd, args, { input })` 完成，便于单测替换。
 */
import { execFile } from 'node:child_process'
import { INJECT_ERROR_REASONS } from './segments.js'

/** 统一的 Injector 接口（文档 §7） */
export const INJECTOR_METHODS = Object.freeze([
  'setClipboard',
  'getClipboard',
  'pressPaste',
  'selectBack',
  'pressDelete',
  'cursorPos',
  'foregroundApp',
])

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** 辅助功能 / 自动化权限中文指引 */
export const PERMISSION_HINT = [
  '未获得辅助功能权限，按键会被 macOS 静默忽略。请按以下步骤授权后重启本程序：',
  '  1) 打开「系统设置」→「隐私与安全性」→「辅助功能」',
  '  2) 勾选运行本程序的宿主程序（Terminal / iTerm / VS Code / 终端里启动的 node）',
  '  3) 首次运行还会弹出「Terminal 想要控制 System Events」，必须点「允许」',
  '  4) 授权后重启本程序（macOS 不会给已运行的进程补权限）',
].join('\n')

/** 把 osascript 的报错翻译成中文提示 */
export function explainOsascriptError(err) {
  if (err?.killed || err?.signal) {
    return 'osascript 执行超时（多半是卡在系统授权弹窗上，或被执行环境拦截）'
  }
  const msg = String(err?.stderr || err?.message || err || '')
  if (msg.includes('-1743') || /not authorized to send apple events/i.test(msg)) {
    return '自动化权限被拒绝（-1743）：系统设置 → 隐私与安全性 → 自动化 → 允许终端控制「System Events」'
  }
  if (msg.includes('-25211') || /not allowed assistive access/i.test(msg)) {
    return '辅助功能权限被拒绝（-25211）'
  }
  if (msg.includes('-10004') || /privilege violation|权限违例/i.test(msg)) {
    return 'System Events 拒绝访问（-10004：权限违例）'
  }
  if (msg.includes('-600') || /application isn't running/i.test(msg)) {
    return 'System Events 未运行（-600）：请确认已登录图形界面（不要在纯 SSH 会话里使用）'
  }
  return msg.trim() || '未知错误'
}

/** 单次 osascript 的默认超时：防止卡在系统授权弹窗上把主流程挂死 */
export const OSA_TIMEOUT_MS = 5000
/** 启动自检的超时（更短，避免启动被弹窗卡住） */
export const SELFCHECK_TIMEOUT_MS = 3500

/**
 * 默认执行器：execFile 封装。
 * @param {string} cmd
 * @param {string[]} args
 * @param {{input?: string|null, timeout?: number}} [opts] input 非 null 时写入子进程 stdin（UTF-8）
 * @returns {Promise<{stdout:string, stderr:string}>}
 */
export function createExecFileRunner({ timeout = 10000, maxBuffer = 4 * 1024 * 1024 } = {}) {
  return function run(cmd, args = [], { input = null, timeout: callTimeout = null } = {}) {
    return new Promise((resolve, reject) => {
      const child = execFile(
        cmd,
        args,
        { timeout: callTimeout ?? timeout, maxBuffer, encoding: 'utf8' },
        (err, stdout, stderr) => {
          if (err) {
            err.stdout = stdout
            err.stderr = stderr
            reject(err)
            return
          }
          resolve({ stdout: stdout ?? '', stderr: stderr ?? '' })
        },
      )
      // pbcopy 提前退出时可能触发 EPIPE，这里吞掉，避免未捕获异常打崩进程
      child.stdin?.on('error', () => {})
      if (input !== null && input !== undefined) child.stdin?.end(String(input), 'utf8')
      else child.stdin?.end()
    })
  }
}

/** 读取光标位置的 AppleScript（文档 §4.2） */
export const CURSOR_POS_SCRIPT = `
tell application "System Events"
  set frontApp to first application process whose frontmost is true
  try
    set el to value of attribute "AXFocusedUIElement" of frontApp
    set r to value of attribute "AXSelectedTextRange" of el
    return item 1 of r
  on error
    return -1
  end try
end tell`.trim()

/** 前台 App 名称的 AppleScript */
export const FOREGROUND_APP_SCRIPT =
  'tell application "System Events" to return name of first application process whose frontmost is true'

/** Cmd+V */
export const PASTE_SCRIPT = 'tell application "System Events" to keystroke "v" using command down'

/**
 * 创建 macOS 注入器。
 * @param {object} [opts]
 * @param {Function} [opts.run] 命令执行器（可注入假实现）
 * @param {Function} [opts.sleep] 延时器
 * @param {number}   [opts.batch] selectBack / pressDelete 每次 osascript 的最大重复次数
 * @param {object}   [opts.logger]
 */
export function createMacInjector({ run = createExecFileRunner(), sleep: wait = sleep, batch = 20, logger = null } = {}) {
  const osa = (script, opts = {}) => run('osascript', ['-e', script], { timeout: OSA_TIMEOUT_MS, ...opts })

  /** 把文本写入系统剪贴板（不经 shell，避免转义问题） */
  async function setClipboard(text) {
    await run('pbcopy', [], { input: String(text ?? '') })
  }

  /** 读取剪贴板 */
  async function getClipboard() {
    const { stdout } = await run('pbpaste', [], {})
    return stdout
  }

  /** 发送 Cmd+V */
  async function pressPaste() {
    await osa(PASTE_SCRIPT)
  }

  /** Shift+← 选中共 n 个 Unicode 字符（分批，用 repeat 降低 osascript 调用次数） */
  async function selectBack(n, step = batch) {
    let left = Math.max(0, Math.floor(Number(n) || 0))
    const size = Math.max(1, Math.floor(Number(step) || batch))
    while (left > 0) {
      const take = Math.min(left, size)
      // key code 123 = ←
      await osa(
        `tell application "System Events" to repeat ${take} times\n  key code 123 using shift down\nend repeat`,
      )
      left -= take
    }
  }

  /** 按 Delete（向前删除）n 次，key code 51 */
  async function pressDelete(n = 1, step = batch) {
    let left = Math.max(0, Math.floor(Number(n) || 0))
    const size = Math.max(1, Math.floor(Number(step) || batch))
    while (left > 0) {
      const take = Math.min(left, size)
      await osa(`tell application "System Events" to repeat ${take} times\n  key code 51\nend repeat`)
      left -= take
    }
  }

  /** 当前光标位置（字符索引）；取不到返回 -1 */
  async function cursorPos() {
    try {
      const { stdout } = await osa(CURSOR_POS_SCRIPT)
      const n = Number.parseInt(String(stdout).trim(), 10)
      return Number.isFinite(n) ? n : -1
    } catch (err) {
      if (logger) logger.warn(`读取光标位置失败（跳过检查）：${explainOsascriptError(err)}`)
      return -1
    }
  }

  /** 前台 App 名称；取不到返回「未知」 */
  async function foregroundApp() {
    try {
      const { stdout } = await osa(FOREGROUND_APP_SCRIPT)
      const name = String(stdout).trim()
      return name || '未知'
    } catch {
      return '未知'
    }
  }

  /**
   * 启动自检：跑一次无害的 osascript，确认辅助功能 / 自动化权限。
   * 带超时——首次运行可能弹出「想要控制 System Events」授权框，
   * 不能让它把启动流程挂死（超时后给出明确中文提示）。
   * @returns {Promise<{ok:boolean, app?:string, error?:string, hint?:string, timedOut?:boolean}>}
   */
  async function checkPermission({ timeoutMs = SELFCHECK_TIMEOUT_MS } = {}) {
    try {
      const { stdout } = await osa(FOREGROUND_APP_SCRIPT, { timeout: timeoutMs })
      return { ok: true, app: String(stdout).trim() || '未知' }
    } catch (err) {
      const timedOut = Boolean(err?.killed || err?.signal)
      return {
        ok: false,
        timedOut,
        error: explainOsascriptError(err),
        hint: timedOut
          ? '自检超时，可能正卡在系统授权弹窗上：请到屏幕上点「允许」，然后重启本程序。'
          : PERMISSION_HINT,
      }
    }
  }

  return {
    platform: 'darwin',
    supported: true,
    setClipboard,
    getClipboard,
    pressPaste,
    selectBack,
    pressDelete,
    cursorPos,
    foregroundApp,
    checkPermission,
  }
}

/** 非 macOS：给出清晰报错（Windows / Linux 为 P2，见文档 §7） */
export function createUnsupportedInjector(platform) {
  const reason =
    `当前平台 ${platform} 暂不支持文本注入。v1 仅支持 macOS（pbcopy + osascript）。` +
    `Windows / Linux 计划在 P2 实现（见 docs/voice-input/桌面客户端.md §7）。`
  const fail = () => {
    const err = new Error(reason)
    err.reason = 'no_focus'
    err.unsupported = true
    return Promise.reject(err)
  }
  return {
    platform,
    supported: false,
    reason,
    setClipboard: fail,
    getClipboard: fail,
    pressPaste: fail,
    selectBack: fail,
    pressDelete: fail,
    cursorPos: async () => -1,
    foregroundApp: async () => '未知',
    checkPermission: async () => ({ ok: false, error: reason, hint: reason, timedOut: false }),
  }
}

/**
 * 按当前平台创建注入器。
 * @param {{platform?:string}} [opts]
 */
export function createInjector({ platform = process.platform, ...opts } = {}) {
  if (platform === 'darwin') return createMacInjector(opts)
  return createUnsupportedInjector(platform)
}

/** 把底层异常归类为协议里的 inject.error.reason */
export function classifyInjectError(err) {
  if (!err) return 'perm_denied'
  if (err.reason && INJECT_ERROR_REASONS.includes(err.reason)) return err.reason
  const text = String(err.stderr || err.message || err)
  if (/secure input|secure event input/i.test(text)) return 'secure_input'
  if (/\bpbcopy\b|\bpbpaste\b|clipboard/i.test(text)) return 'clipboard_failed'
  if (err.code === 'ENOENT') return 'clipboard_failed'
  if (/no focus|no_focus|invalid element|AXFocusedUIElement/i.test(text)) return 'no_focus'
  return 'perm_denied'
}

/**
 * 剪贴板守卫：注入前保存用户原剪贴板，注入后延时还原。
 *
 * 还原前重新读取比对：只有剪贴板里**仍然是本次注入写入的内容**时才还原，
 * 否则说明用户自己复制了新东西，**放弃还原**（避免覆盖用户的复制）。
 * （注意：不能拿当前值和「原内容」比对——注入后它必然不同，那样永远都不会还原。）
 */
export function createClipboardGuard({ inj, restoreDelayMs = 300, sleep: wait = sleep, logger = null, now = () => Date.now() }) {
  let saved = null
  let savedAt = 0
  let written = null
  let restoreTimer = null
  let token = 0

  /** 注入前调用：仅在「本次注入周期」第一次调用时保存 */
  async function capture() {
    cancelRestore()
    if (saved !== null) return saved
    try {
      saved = await inj.getClipboard()
      written = null
      savedAt = now()
    } catch (err) {
      saved = null
      if (logger) logger.warn(`读取原剪贴板失败，本次不还原剪贴板：${err.message}`)
    }
    return saved
  }

  /** 注入实际写入剪贴板的内容（用于还原前比对） */
  function noteWritten(text) {
    written = String(text ?? '')
  }

  function cancelRestore() {
    if (restoreTimer) {
      clearTimeout(restoreTimer)
      restoreTimer = null
    }
    token += 1
  }

  /**
   * 立即还原（可单测）。
   * @returns {Promise<{restored:boolean, skipped?:string}>}
   */
  async function restoreNow() {
    if (saved === null) return { restored: false, skipped: 'nothing_saved' }
    const original = saved
    const mine = written
    try {
      const current = await inj.getClipboard()
      if (mine !== null && current !== mine) {
        if (logger) logger.info('剪贴板在还原前已被改动，放弃还原（避免覆盖你自己的复制）')
        saved = null
        written = null
        return { restored: false, skipped: 'changed' }
      }
      saved = null
      written = null
      if (current === original) return { restored: false, skipped: 'already' }
      await inj.setClipboard(original)
      return { restored: true }
    } catch (err) {
      saved = null
      written = null
      if (logger) logger.warn(`还原剪贴板失败：${err.message}`)
      return { restored: false, skipped: 'error' }
    }
  }

  /** 注入后调用：延时还原，不阻塞注入主流程 */
  function scheduleRestore() {
    cancelRestore()
    if (saved === null) return
    const myToken = token
    restoreTimer = setTimeout(() => {
      restoreTimer = null
      if (myToken !== token) return // 期间又有注入 → 交给新的还原
      restoreNow().catch(() => {})
    }, restoreDelayMs)
    restoreTimer.unref?.()
  }

  return {
    capture,
    noteWritten,
    restoreNow,
    scheduleRestore,
    cancelRestore,
    get saved() {
      return saved
    },
    get written() {
      return written
    },
    get savedAt() {
      return savedAt
    },
    reset() {
      cancelRestore()
      saved = null
      written = null
    },
  }
}

/**
 * 执行一次注入动作（原子编排，可单测）。
 *
 * - insert / append：写剪贴板 → Cmd+V（纯追加只要一次粘贴，极快）
 * - replace        ：写剪贴板 → Shift+← 选中旧尾部 → Cmd+V（选中即被覆盖，原子替换）
 * - delete         ：Shift+← 选中旧尾部 → Delete（纯回删，不动剪贴板）
 *
 * @param {object} plan segments.js 产出的动作
 * @param {object} ctx
 * @param {object} ctx.inj 注入器
 * @param {object} [ctx.clipboard] 剪贴板守卫
 * @param {boolean} [ctx.clipboardRestore] 是否还原剪贴板
 * @param {object} [ctx.logger]
 * @returns {Promise<{action:'inserted'|'appended'|'replaced'|'deleted'|'noop', delta:number, pasted:string, selectedBack:number}>}
 */
export async function applyPlan(plan, { inj, clipboard = null, clipboardRestore = true, logger = null } = {}) {
  const guard = clipboardRestore ? clipboard : null

  /** 包一层：写剪贴板的动作需要保护用户剪贴板 */
  async function withClipboard(fn, writtenText) {
    if (guard) await guard.capture()
    try {
      await fn()
    } finally {
      if (guard) {
        guard.noteWritten(writtenText)
        guard.scheduleRestore()
      }
    }
  }

  if (!plan || plan.kind === 'skip') {
    return { action: 'noop', delta: 0, pasted: '', selectedBack: 0 }
  }

  if (plan.kind === 'insert' || plan.kind === 'append') {
    const pasted = String(plan.tailNew ?? plan.text ?? '')
    if (pasted === '') return { action: 'noop', delta: 0, pasted: '', selectedBack: 0 }
    await withClipboard(async () => {
      await inj.setClipboard(pasted)
      await inj.pressPaste()
    }, pasted)
    if (logger) logger.info(`已粘贴 ${pasted.length} 字节文本（${plan.kind === 'insert' ? '整段插入' : '追加'}）`)
    return {
      action: plan.kind === 'insert' ? 'inserted' : 'appended',
      delta: Array.from(pasted).length,
      pasted,
      selectedBack: 0,
    }
  }

  if (plan.kind === 'replace') {
    const pasted = String(plan.tailNew ?? '')
    await withClipboard(async () => {
      await inj.setClipboard(pasted)
      await inj.selectBack(plan.tailOld)
      await inj.pressPaste()
    }, pasted)
    return {
      action: 'replaced',
      delta: Array.from(pasted).length - plan.tailOld,
      pasted,
      selectedBack: plan.tailOld,
    }
  }

  if (plan.kind === 'delete') {
    await inj.selectBack(plan.tailOld)
    await inj.pressDelete()
    return { action: 'deleted', delta: -plan.tailOld, pasted: '', selectedBack: plan.tailOld }
  }

  return { action: 'noop', delta: 0, pasted: '', selectedBack: 0 }
}
