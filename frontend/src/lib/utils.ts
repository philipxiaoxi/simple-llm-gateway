import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const normalized = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`
  return new Date(normalized).toLocaleString('zh-CN', {
    hour12: false,
  })
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
