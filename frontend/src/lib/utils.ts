import { clsx, type ClassValue } from 'clsx'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import { twMerge } from 'tailwind-merge'

dayjs.extend(utc)

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.left = '-9999px'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    if (!ok) throw new Error('浏览器拒绝写入剪贴板')
  }
}

export const MIN_PASSWORD_LENGTH = 8

export const RISK_LEVELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad'; hint: string }> = {
  low: { label: '低风险', tone: 'ok', hint: '官方模型或数据泄露可能性较低' },
  medium: { label: '中风险', tone: 'warn', hint: '非官方，可能是中转站或内部部署的模型' },
  high: { label: '高风险', tone: 'bad', hint: '非官方的低价或廉价站点模型，可能存在信息收集' },
}
export const MIN_KEY_LENGTH = 8
export const LOG_PAGE_SIZE = 20
export const ALIAS_INPUT_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$/

const ISO_TIME = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?/g

export function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? dayjs(value) : dayjs.utc(value)
  if (!parsed.isValid()) return value
  return parsed.local().format('YYYY-MM-DD HH:mm:ss')
}

export function formatEmbeddedTimes(value: string) {
  return value.replace(ISO_TIME, (match) => formatTime(match))
}

export function formatTokenCount(value: number | null | undefined) {
  if (value == null) return '—'
  if (value < 1_000) return String(value)
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
}

export function formatContextWindow(value: number | null | undefined) {
  if (value == null || value <= 0) return '—'
  if (value % 1_000_000 === 0) return `${value / 1_000_000}M`
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (value % 1_000 === 0) return `${value / 1_000}K`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`
  return String(value)
}

export function modelCapsHint(caps?: {
  context_window?: number | null
  max_output_tokens?: number | null
  reasoning?: boolean
  reasoning_efforts?: string[] | null
  modalities?: { input?: string[] | null } | null
} | null) {
  if (!caps) return ''
  const extras = [
    caps.context_window ? `上下文 ${formatContextWindow(caps.context_window)}` : null,
    caps.max_output_tokens ? `输出 ${formatContextWindow(caps.max_output_tokens)}` : null,
    caps.reasoning ? (caps.reasoning_efforts?.length ? `思考 ${caps.reasoning_efforts.join('/')}` : '思考') : null,
    caps.modalities?.input?.includes('image') ? '视觉' : null,
  ].filter(Boolean)
  return extras.join(' · ')
}

export function formatBytes(value: number | null | undefined) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1).replace(/\.0$/, '')} KB`
  return `${(value / (1024 * 1024)).toFixed(1).replace(/\.0$/, '')} MB`
}

export function formatTokens(item: {
  total_tokens?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
}) {
  if (item.total_tokens != null) return formatTokenCount(item.total_tokens)
  if (item.prompt_tokens != null || item.completion_tokens != null) {
    return formatTokenCount((item.prompt_tokens || 0) + (item.completion_tokens || 0))
  }
  return '—'
}
