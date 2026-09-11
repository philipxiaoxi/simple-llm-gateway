/**
 * config.js —— 配置读取与合并
 *
 * 优先级：命令行参数 > 环境变量 > 配置文件(~/.llm-gateway-voice.json) > 内置默认值
 * 环境变量：VOICE_DESKTOP_TOKEN / VOICE_SERVER_URL / VOICE_ROOM_ID
 * clientUid 为空时自动生成 `dc_<uuid>` 并写回配置文件，保证重连后仍是同一台设备。
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import crypto from 'node:crypto'

/** 内置默认值（与 config.example.json 保持一致） */
export const DEFAULT_CONFIG = Object.freeze({
  serverUrl: 'wss://你的站/api/voice/desktop/connect',
  roomId: '',
  token: '',
  clientUid: '',
  name: '',
  insertMode: false,
  clipboardRestore: true,
  replaceEnabled: true,
  commitDelayMs: 600,
  pollIntervalMs: 300,
  logFile: '~/.llm-gateway-voice.log',
})

/** 默认配置文件路径 */
export function defaultConfigPath(home = os.homedir()) {
  return path.join(home, '.llm-gateway-voice.json')
}

/** 展开 `~` 前缀 */
export function expandHome(p, home = os.homedir()) {
  if (typeof p !== 'string' || p === '') return p
  if (p === '~') return home
  if (p.startsWith('~/')) return path.join(home, p.slice(2))
  return p
}

/** 生成设备 ID */
export function generateClientUid() {
  return `dc_${crypto.randomUUID()}`
}

/** 日志里脱敏显示令牌 */
export function maskSecret(secret) {
  const s = String(secret ?? '')
  if (s.length <= 8) return s ? '****' : ''
  return `${s.slice(0, 4)}……${s.slice(-4)}`
}

export const USAGE = `
voice-desktop —— 手机语音输入 · 电脑端客户端

用法：
  node src/index.js [选项]

选项：
  --init                     交互式生成配置文件（~/.llm-gateway-voice.json）
  -h, --help                 显示本帮助
  --config <路径>            指定配置文件（默认 ~/.llm-gateway-voice.json）
  --server-url <wss://…>     服务端地址（完整 URL，含 /api/voice/desktop/connect）
  --room <roomId>            房间 ID
  --token <token>            房间令牌（或全局 VOICE_DESKTOP_TOKEN）
  --name <名称>              本机显示名（默认取主机名）
  --insert-mode              启动时即打开插入模式
  --no-insert-mode           启动时保持插入模式关闭（默认）
  --no-replace               只追加、不做原地替换
  --no-clipboard-restore     注入后不还原剪贴板
  --log-file <路径>          日志文件（默认 ~/.llm-gateway-voice.log）

环境变量：
  VOICE_SERVER_URL           同 --server-url
  VOICE_ROOM_ID              同 --room
  VOICE_DESKTOP_TOKEN        同 --token

运行中单键命令（无需回车）：
  i  切换插入模式      s  查看状态      c  清空账本      r  立即重连      q  退出
`.trimStart()

/** 取下一个参数值（支持 `--key value` 与 `--key=value`） */
function takeValue(argv, i, key) {
  const cur = argv[i]
  const eq = cur.indexOf('=')
  if (eq >= 0) return { value: cur.slice(eq + 1), next: i + 1 }
  const next = argv[i + 1]
  if (next === undefined || next.startsWith('--')) return { value: '', next: i + 1 }
  return { value: next, next: i + 2 }
}

/**
 * 解析命令行参数。
 * @returns {{help:boolean, init:boolean, configPath:string|null, overrides:object, unknown:string[]}}
 */
export function parseArgv(argv = []) {
  const out = { help: false, init: false, configPath: null, overrides: {}, unknown: [] }
  let i = 0
  while (i < argv.length) {
    const key = argv[i].split('=')[0]
    switch (key) {
      case '-h':
      case '--help':
        out.help = true
        i += 1
        break
      case '--init':
        out.init = true
        i += 1
        break
      case '--config': {
        const { value, next } = takeValue(argv, i, key)
        out.configPath = value || null
        i = next
        break
      }
      case '--server-url': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.serverUrl = value
        i = next
        break
      }
      case '--room':
      case '--room-id': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.roomId = value
        i = next
        break
      }
      case '--token': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.token = value
        i = next
        break
      }
      case '--name': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.name = value
        i = next
        break
      }
      case '--insert-mode':
        out.overrides.insertMode = true
        i += 1
        break
      case '--no-insert-mode':
        out.overrides.insertMode = false
        i += 1
        break
      case '--replace':
        out.overrides.replaceEnabled = true
        i += 1
        break
      case '--no-replace':
        out.overrides.replaceEnabled = false
        i += 1
        break
      case '--clipboard-restore':
        out.overrides.clipboardRestore = true
        i += 1
        break
      case '--no-clipboard-restore':
        out.overrides.clipboardRestore = false
        i += 1
        break
      case '--log-file': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.logFile = value
        i = next
        break
      }
      case '--commit-delay': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.commitDelayMs = Number(value)
        i = next
        break
      }
      case '--poll-interval': {
        const { value, next } = takeValue(argv, i, key)
        out.overrides.pollIntervalMs = Number(value)
        i = next
        break
      }
      default:
        out.unknown.push(argv[i])
        i += 1
    }
  }
  return out
}

/** 从环境变量取覆盖项 */
export function envOverrides(env = process.env) {
  const o = {}
  if (env.VOICE_SERVER_URL) o.serverUrl = env.VOICE_SERVER_URL
  if (env.VOICE_ROOM_ID) o.roomId = env.VOICE_ROOM_ID
  if (env.VOICE_DESKTOP_TOKEN) o.token = env.VOICE_DESKTOP_TOKEN
  return o
}

/** 读取配置文件；不存在或损坏时返回 ok:false 而不抛异常 */
export function readConfigFile(file) {
  try {
    const raw = fs.readFileSync(file, 'utf8')
    const data = JSON.parse(raw)
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { ok: false, exists: true, data: {}, error: '配置文件内容不是 JSON 对象' }
    }
    return { ok: true, exists: true, data, error: null }
  } catch (err) {
    if (err && err.code === 'ENOENT') return { ok: false, exists: false, data: {}, error: null }
    return { ok: false, exists: true, data: {}, error: `配置文件解析失败：${err.message}` }
  }
}

/** 写入配置文件（2 空格缩进），失败返回错误信息 */
export function writeConfigFile(file, data) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
    return null
  } catch (err) {
    return err.message
  }
}

/** 数值兜底 */
function toInt(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? Math.round(n) : fallback
}

/**
 * 载入并合并配置。
 * @param {object} [opts]
 * @param {string[]} [opts.argv] 命令行参数（不含 node/脚本名）
 * @param {object}   [opts.env]  环境变量
 * @param {string}   [opts.home] home 目录
 * @param {boolean}  [opts.persistUid] 是否把自动生成的 clientUid 写回配置文件
 * @returns {{config:object, configPath:string, parsed:object, sources:object,
 *            problems:string[], warnings:string[], uidGenerated:boolean, configExists:boolean}}
 */
export function loadConfig({ argv = process.argv.slice(2), env = process.env, home = os.homedir(), persistUid = true, hostname = os.hostname() } = {}) {
  const parsed = parseArgv(argv)
  const configPath = expandHome(parsed.configPath || defaultConfigPath(home), home)
  const file = readConfigFile(configPath)
  const envs = envOverrides(env)

  const config = { ...DEFAULT_CONFIG }
  const sources = {}
  const warnings = []

  // 1) 配置文件
  for (const key of Object.keys(DEFAULT_CONFIG)) {
    if (file.ok && file.data[key] !== undefined && file.data[key] !== null) {
      config[key] = file.data[key]
      sources[key] = '配置文件'
    } else {
      sources[key] = '默认值'
    }
  }
  if (file.error) warnings.push(file.error)

  // 2) 环境变量
  for (const [key, value] of Object.entries(envs)) {
    if (value !== undefined && value !== '') {
      config[key] = value
      sources[key] = '环境变量'
    }
  }

  // 3) 命令行
  for (const [key, value] of Object.entries(parsed.overrides)) {
    if (value === undefined) continue
    config[key] = value
    sources[key] = '命令行'
  }

  // 规范化
  config.serverUrl = String(config.serverUrl || '').trim()
  config.roomId = String(config.roomId || '').trim()
  config.token = String(config.token || '').trim()
  config.clientUid = String(config.clientUid || '').trim()
  config.name = String(config.name || '').trim()
  config.logFile = expandHome(String(config.logFile || '').trim(), home)
  config.insertMode = Boolean(config.insertMode)
  config.clipboardRestore = Boolean(config.clipboardRestore)
  config.replaceEnabled = Boolean(config.replaceEnabled)
  config.commitDelayMs = toInt(config.commitDelayMs, DEFAULT_CONFIG.commitDelayMs)
  config.pollIntervalMs = toInt(config.pollIntervalMs, DEFAULT_CONFIG.pollIntervalMs)

  // 主机名兜底
  if (!config.name) {
    config.name = hostname
    sources.name = sources.name === '默认值' ? '默认值(主机名)' : `${sources.name}(空→主机名)`
  }

  // 校验
  const problems = []
  if (!config.serverUrl || config.serverUrl === DEFAULT_CONFIG.serverUrl) {
    problems.push('缺少 serverUrl（服务端 WebSocket 完整地址，形如 wss://你的站/api/voice/desktop/connect）')
  } else if (!/^wss?:\/\//i.test(config.serverUrl)) {
    problems.push(`serverUrl 必须以 ws:// 或 wss:// 开头，当前为「${config.serverUrl}」`)
  }
  if (!config.roomId) problems.push('缺少 roomId（房间 ID）')
  if (!config.token) problems.push('缺少 token（房间令牌或全局 VOICE_DESKTOP_TOKEN）')

  // clientUid 自动生成并写回（配置齐全或配置文件已存在时才落盘，避免启动失败还留垃圾文件）
  let uidGenerated = false
  if (!config.clientUid) {
    config.clientUid = generateClientUid()
    sources.clientUid = '自动生成'
    uidGenerated = true
    if (persistUid && (file.exists || problems.length === 0)) {
      const next = { ...(file.ok ? file.data : {}), clientUid: config.clientUid }
      const err = writeConfigFile(configPath, next)
      if (err) warnings.push(`clientUid 写回配置文件失败（${err}），本次仍使用 ${config.clientUid}`)
    } else if (persistUid) {
      warnings.push(`clientUid 本次临时生成（${config.clientUid}），配置补全后会自动写回 ${configPath}`)
    }
  }

  return { config, configPath, parsed, sources, problems, warnings, uidGenerated, configExists: file.exists }
}

/** 生成配置文件模板（--init 使用） */
export function configTemplate(overrides = {}) {
  return { ...DEFAULT_CONFIG, ...overrides, logFile: DEFAULT_CONFIG.logFile }
}

/**
 * `--init`：交互式生成配置文件。
 * 非 TTY（管道/CI）时直接写出模板并返回，不阻塞。
 * @returns {Promise<{path:string, written:boolean, interactive:boolean}>}
 */
export async function initConfig({ configPath, stdout = process.stdout, stdin = process.stdin } = {}) {
  const target = expandHome(configPath || defaultConfigPath())
  const existing = readConfigFile(target)
  const base = existing.ok ? { ...existing.data } : {}

  const interactive = Boolean(stdin.isTTY && stdout.isTTY)
  const patch = {}

  if (interactive) {
    const readline = await import('node:readline/promises')
    const rl = readline.createInterface({ input: stdin, output: stdout })
    const ask = async (label, current, fallback = '') => {
      const hint = current ? `（当前：${current}）` : fallback ? `（回车使用 ${fallback}）` : ''
      const answer = (await rl.question(`${label}${hint}: `)).trim()
      return answer || current || fallback
    }
    try {
      stdout.write('\n即将生成配置文件，直接回车表示保持默认/当前值。\n\n')
      patch.serverUrl = await ask('服务端地址 serverUrl', base.serverUrl, DEFAULT_CONFIG.serverUrl)
      patch.roomId = await ask('房间 ID roomId', base.roomId)
      patch.token = await ask('房间令牌 token', base.token)
      patch.name = await ask('本机显示名 name', base.name, os.hostname())
      const im = await ask('启动时是否打开插入模式 insertMode (y/N)', base.insertMode ? 'y' : '', 'n')
      patch.insertMode = /^y(es)?$/i.test(im)
      const re = await ask('允许原地替换 replaceEnabled (Y/n)', base.replaceEnabled === false ? 'n' : 'y', 'y')
      patch.replaceEnabled = !/^n(o)?$/i.test(re)
      patch.logFile = await ask('日志文件 logFile', base.logFile, DEFAULT_CONFIG.logFile)
    } finally {
      rl.close()
    }
  } else {
    stdout.write('\n当前不是交互式终端，将写出配置模板，请手动填写。\n\n')
  }

  const merged = { ...configTemplate(), ...base, ...patch }
  if (!merged.clientUid) merged.clientUid = generateClientUid()
  // 保持字段顺序与 config.example.json 一致
  const ordered = {}
  for (const key of Object.keys(DEFAULT_CONFIG)) ordered[key] = merged[key]

  const err = writeConfigFile(target, ordered)
  if (err) {
    const e = new Error(`写入配置文件失败：${err}`)
    e.code = 'CONFIG_WRITE_FAILED'
    throw e
  }
  return { path: target, written: true, interactive }
}
