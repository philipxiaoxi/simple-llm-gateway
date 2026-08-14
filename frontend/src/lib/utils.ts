import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  return new Date(value.endsWith('Z') ? value : value + 'Z').toLocaleString('zh-CN', {
    hour12: false,
  })
}
