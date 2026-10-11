import type { GenerationModel, GenerationTask } from './api'

export type StatusTone = 'ok' | 'bad' | 'warn' | 'mist' | 'info'

export const GENERATION_RUNNING_STATUSES = ['queued', 'submitting', 'running'] as const

export function isTaskActive(status: string) {
  return (GENERATION_RUNNING_STATUSES as readonly string[]).includes(status)
}

export function statusLabel(status: string) {
  switch (status) {
    case 'queued':
      return '排队中'
    case 'submitting':
      return '提交中'
    case 'running':
      return '生成中'
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'violation':
      return '违规拦截'
    default:
      return status || '未知'
  }
}

export function statusTone(status: string): StatusTone {
  switch (status) {
    case 'succeeded':
      return 'ok'
    case 'failed':
      return 'bad'
    case 'violation':
      return 'warn'
    case 'running':
    case 'submitting':
      return 'info'
    default:
      return 'mist'
  }
}

/** 模型的参数默认值（用于切换模型时初始化表单）。 */
export function defaultParams(model: GenerationModel): Record<string, string | number> {
  const result: Record<string, string | number> = {}
  for (const param of model.params) {
    if (param.type === 'number') {
      result[param.key] = param.default === null || param.default === undefined ? '' : Number(param.default)
    } else {
      result[param.key] = param.default === null || param.default === undefined ? '' : String(param.default)
    }
  }
  return result
}

/** 把生成参数压成一行展示文案。 */
export function taskParamSummary(task: GenerationTask): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(task.params || {})) {
    if (value === '' || value === null || value === undefined) continue
    if (key === 'seed') continue
    parts.push(String(value))
  }
  return parts.join(' · ')
}

export function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`
}

export function formatTime(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function resultThumb(task: GenerationTask) {
  return task.results.find((asset) => asset.status === 'ready' && asset.url)
}
