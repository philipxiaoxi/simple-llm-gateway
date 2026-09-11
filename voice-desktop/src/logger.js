/**
 * logger.js —— 控制台彩色中文日志 + 文件日志
 *
 * 控制台格式（参考 桌面客户端.md §6）：
 *   [10:00:01] ✓ 已连接 wss://…
 *   [10:00:04] ← rev0 「你好我们」 追加「你好我们」 12ms
 * 文件日志：追加写入 logFile，按天轮转，保留 7 天；写文件失败绝不影响主流程。
 */
import fs from 'node:fs'
import path from 'node:path'

/** ANSI 颜色 */
const C = {
  reset: '\u001b[0m',
  bold: '\u001b[1m',
  dim: '\u001b[2m',
  red: '\u001b[31m',
  green: '\u001b[32m',
  yellow: '\u001b[33m',
  blue: '\u001b[34m',
  magenta: '\u001b[35m',
  cyan: '\u001b[36m',
  gray: '\u001b[90m',
}

/** 默认保留天数 */
export const KEEP_DAYS = 7

/** `HH:MM:SS` */
export function formatTime(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** `YYYY-MM-DD` */
export function formatDate(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** `YYYY-MM-DD HH:MM:SS` */
export function formatDateTime(d = new Date()) {
  return `${formatDate(d)} ${formatTime(d)}`
}

/** 去掉 ANSI 颜色，用于写文件 */
export function stripAnsi(s) {
  // eslint-disable-next-line no-control-regex
  return String(s).replace(/\u001b\[[0-9;]*m/g, '')
}

/** 把文本截断到 n 个 Unicode 字符（emoji 安全），超出加省略号 */
export function clip(text, n = 60) {
  const chars = Array.from(String(text ?? ''))
  if (chars.length <= n) return chars.join('')
  return `${chars.slice(0, n).join('')}…`
}

/** 把含换行/制表符的文本压成单行，便于日志展示 */
export function oneLine(text) {
  return String(text ?? '')
    .replace(/\r?\n/g, '⏎')
    .replace(/\t/g, '→')
}

/**
 * 轮转：若当前日志文件的最后修改日期不是今天，则改名为 `<file>.<YYYY-MM-DD>`。
 * 返回是否存在可写目录。
 */
function rotateIfNeeded(file, now) {
  const today = formatDate(now)
  let st = null
  try {
    st = fs.statSync(file)
  } catch {
    return // 文件不存在 → 无需轮转
  }
  if (formatDate(st.mtime) === today) return
  const base = `${file}.${formatDate(st.mtime)}`
  let target = base
  let i = 1
  while (fs.existsSync(target)) target = `${base}.${i++}`
  try {
    fs.renameSync(file, target)
  } catch {
    /* 轮转失败不影响继续写 */
  }
}

/** 清理超过 keepDays 天的轮转文件 */
function pruneOld(file, now, keepDays) {
  const dir = path.dirname(file)
  const prefix = `${path.basename(file)}.`
  let names = []
  try {
    names = fs.readdirSync(dir)
  } catch {
    return
  }
  const deadline = now.getTime() - keepDays * 24 * 3600 * 1000
  for (const name of names) {
    if (!name.startsWith(prefix)) continue
    const full = path.join(dir, name)
    try {
      const st = fs.statSync(full)
      if (st.isFile() && st.mtime.getTime() < deadline) fs.unlinkSync(full)
    } catch {
      /* 忽略单个文件错误 */
    }
  }
}

/**
 * 创建日志器。
 * @param {object} opts
 * @param {string|null} opts.logFile 日志文件绝对路径（null 表示只输出控制台）
 * @param {boolean} [opts.color] 是否使用颜色，默认按 TTY 自动判断
 * @param {NodeJS.WriteStream} [opts.stream] 输出流，默认 stdout
 * @param {() => Date} [opts.now] 时间源（便于测试）
 */
export function createLogger({ logFile = null, color = null, stream = process.stdout, now = () => new Date(), keepDays = KEEP_DAYS } = {}) {
  const useColor = color === null ? Boolean(stream?.isTTY) && !process.env.NO_COLOR : Boolean(color)
  const file = logFile ? path.resolve(logFile) : null
  let fileBroken = false

  if (file) {
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true })
      rotateIfNeeded(file, now())
      pruneOld(file, now(), keepDays)
    } catch {
      fileBroken = true
    }
  }

  const paint = (c, s) => (useColor ? `${c}${s}${C.reset}` : s)

  function writeFile(plain, level) {
    if (!file || fileBroken) return
    try {
      rotateIfNeeded(file, now())
      fs.appendFileSync(file, `[${formatDateTime(now())}] [${level}] ${plain}\n`, 'utf8')
    } catch {
      // 文件日志失败降级为仅控制台，不抛出
      fileBroken = true
    }
  }

  /** 输出一行：colored 给控制台，plain 给文件（缺省时去掉颜色） */
  function line(colored, level = 'INFO', plain = null) {
    const text = plain === null ? stripAnsi(colored) : plain
    try {
      stream.write(`${colored}\n`)
    } catch {
      /* 终端写失败忽略 */
    }
    writeFile(text, level)
  }

  const stamp = () => paint(C.gray, `[${formatTime(now())}]`)

  return {
    file,
    color: useColor,
    /** 原始一行（自行着色） */
    raw(text, level = 'INFO') {
      line(text, level)
    },
    /** ⓘ 普通信息 */
    info(msg) {
      line(`${stamp()} ${paint(C.cyan, 'ⓘ')} ${msg}`, 'INFO')
    },
    /** ✓ 成功 */
    ok(msg) {
      line(`${stamp()} ${paint(C.green, '✓')} ${msg}`, 'OK')
    },
    /** ← 收到服务端消息 */
    recv(msg) {
      line(`${stamp()} ${paint(C.blue, '←')} ${msg}`, 'RECV')
    },
    /** → 发往服务端 */
    send(msg) {
      line(`${stamp()} ${paint(C.magenta, '→')} ${msg}`, 'SEND')
    },
    /** ⚠ 警告 */
    warn(msg) {
      line(`${stamp()} ${paint(C.yellow, '⚠')} ${msg}`, 'WARN')
    },
    /** ✗ 错误 */
    error(msg) {
      line(`${stamp()} ${paint(C.red, '✗')} ${msg}`, 'ERROR')
    },
    /** ⏳ 待插入（插入模式关闭时） */
    pending(msg) {
      line(`${stamp()} ${paint(C.yellow, '⏳')} ${msg}`, 'PENDING')
    },
    /** 分节标题 */
    section(title) {
      line(paint(C.bold, title), 'INFO', title)
    },
    /** 关闭（当前实现为同步追加写，无需 flush，保留接口） */
    close() {},
  }
}

/** 无副作用的空日志器（测试/库调用方使用） */
export function createNullLogger() {
  const noop = () => {}
  return {
    file: null,
    color: false,
    raw: noop,
    info: noop,
    ok: noop,
    recv: noop,
    send: noop,
    warn: noop,
    error: noop,
    pending: noop,
    section: noop,
    close: noop,
  }
}
