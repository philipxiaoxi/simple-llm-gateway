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
