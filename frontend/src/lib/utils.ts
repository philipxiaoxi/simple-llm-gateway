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

export const MIN_PASSWORD_LENGTH = 8

export const RISK_LEVELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad'; hint: string }> = {
  low: { label: '低风险', tone: 'ok', hint: '官方模型或数据泄露可能性较低' },
  medium: { label: '中风险', tone: 'warn', hint: '非官方，可能是中转站或内部部署的模型' },
  high: { label: '高风险', tone: 'bad', hint: '非官方的低价或廉价站点模型，可能存在信息收集' },
}
export const MIN_KEY_LENGTH = 8
export const LOG_PAGE_SIZE = 20

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

export function formatTokens(item: {
  total_tokens?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
}) {
  if (item.total_tokens != null) return String(item.total_tokens)
  if (item.prompt_tokens != null || item.completion_tokens != null) {
    return String((item.prompt_tokens || 0) + (item.completion_tokens || 0))
  }
  return '—'
}
