import type { LogItem, LogMessageItem } from './api'
import { formatTime } from './utils'

export const PROTOCOL_LABELS: Record<string, string> = {
  openai_chat: 'OpenAI Chat',
  openai_responses: 'Responses',
  anthropic_messages: 'Anthropic',
}

export const PROTOCOL_ENTRY: Record<string, string> = {
  openai_chat: 'POST /v1/chat/completions',
  openai_responses: 'POST /v1/responses',
  anthropic_messages: 'POST /v1/messages',
}

export function protocolLabel(protocol: string) {
  return PROTOCOL_LABELS[protocol] || protocol
}

const COLLAPSE_CHAR_LIMIT = 400
const COLLAPSE_LINE_LIMIT = 8

export function isLongText(text: string) {
  return text.length > COLLAPSE_CHAR_LIMIT || text.split('\n').length > COLLAPSE_LINE_LIMIT
}

export function protocolEntry(protocol: string) {
  return PROTOCOL_ENTRY[protocol] || protocol
}

export type Tone = 'ok' | 'bad' | 'warn' | 'mist' | 'info'

export function statusMeta(status: string): { label: string; tone: Tone } {
  if (status === 'success') return { label: '完成', tone: 'ok' }
  if (status === 'error') return { label: '失败', tone: 'bad' }
  return { label: status || '未知', tone: 'mist' }
}

export function accountSourceLabel(source: 'upstream' | 'agent' | string) {
  return source === 'agent' ? '[网关]' : '[上游]'
}

export function accountText(item: Pick<LogItem, 'account_name' | 'account_source'>) {
  return item.account_name ? `${accountSourceLabel(item.account_source)} ${item.account_name}` : '—'
}

export function sessionTitle(item: Pick<LogItem, 'model' | 'api_key_name'>) {
  return item.model || item.api_key_name || '未命名会话'
}

const SHANGHAI_OFFSET_HOURS = 8

function shanghaiDayKey(value: string | null | undefined) {
  if (!value) return ''
  const parsed = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? new Date(value) : new Date(`${value}Z`)
  if (Number.isNaN(parsed.valueOf())) return ''
  const shifted = new Date(parsed.getTime() + SHANGHAI_OFFSET_HOURS * 3600 * 1000)
  return shifted.toISOString().slice(0, 10)
}

export function dateGroupLabel(value: string | null | undefined) {
  const key = shanghaiDayKey(value)
  if (!key) return '更早'
  const today = shanghaiDayKey(new Date().toISOString())
  const yesterday = shanghaiDayKey(new Date(Date.now() - 86400000).toISOString())
  if (key === today) return '今天'
  if (key === yesterday) return '昨天'
  return key
}

export type DateGroup<T> = { key: string; label: string; items: T[] }

export function groupByDate<T extends { created_at: string; updated_at?: string | null }>(items: T[]): DateGroup<T>[] {
  const groups: DateGroup<T>[] = []
  const index = new Map<string, DateGroup<T>>()
  for (const item of items) {
    const key = shanghaiDayKey(item.updated_at || item.created_at)
    let group = index.get(key)
    if (!group) {
      group = { key, label: dateGroupLabel(item.updated_at || item.created_at), items: [] }
      index.set(key, group)
      groups.push(group)
    }
    group.items.push(item)
  }
  return groups
}

export type ToolCall = { id: string; name: string; arguments: string }

export type ConversationMessage = {
  seq: number
  role: string
  text: string
  toolCalls: ToolCall[]
  reasoning: { id: string; text: string }[]
}

export type ConversationTurn = {
  key: string
  index: number | null
  messages: ConversationMessage[]
}

export function asText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part
        if (part && typeof part === 'object' && 'text' in part) return String((part as { text?: string }).text || '')
        return JSON.stringify(part)
      })
      .filter(Boolean)
      .join('\n')
  }
  if (content == null) return ''
  return JSON.stringify(content, null, 2)
}

export function normalizeToolCalls(raw: unknown): ToolCall[] {
  if (!Array.isArray(raw)) return []
  const calls: ToolCall[] = []
  raw.forEach((item, position) => {
    if (!item || typeof item !== 'object') return
    const record = item as Record<string, unknown>
    const fn = (record.function as Record<string, unknown> | undefined) ?? undefined
    const name = String((fn?.name as string) ?? record.name ?? '').trim()
    const argsRaw = fn?.arguments ?? record.arguments ?? record.input
    const args = typeof argsRaw === 'string' ? argsRaw : argsRaw == null ? '' : JSON.stringify(argsRaw, null, 2)
    const id = String(record.id ?? record.call_id ?? `call-${position}`)
    if (!name && !args) return
    calls.push({ id, name: name || '工具调用', arguments: args })
  })
  return calls
}

function toConversationMessage(message: LogMessageItem, reasoning: Record<string, string>): ConversationMessage {
  const toolCalls = normalizeToolCalls(message.tool_calls)
  const reasoningEntries = toolCalls
    .filter((call) => reasoning[call.id])
    .map((call) => ({ id: call.id, text: reasoning[call.id] }))
  return {
    seq: message.seq,
    role: message.role || 'user',
    text: asText(message.content),
    toolCalls,
    reasoning: reasoningEntries,
  }
}

export function buildTurns(messages: LogMessageItem[], reasoning: Record<string, string> = {}): ConversationTurn[] {
  const sorted = [...messages].sort((left, right) => left.seq - right.seq)
  const turns: ConversationTurn[] = []
  let current: ConversationTurn | null = null
  for (const message of sorted) {
    const converted = toConversationMessage(message, reasoning)
    if (message.role === 'user' || current === null) {
      current = { key: `turn-${converted.seq}`, index: null, messages: [] }
      turns.push(current)
    }
    current.messages.push(converted)
  }
  let counter = 0
  for (const turn of turns) {
    if (turn.messages[0]?.role === 'user') {
      counter += 1
      turn.index = counter
    }
  }
  return turns
}

export type ExecutionStep = { key: string; label: string; detail: string; tone: Tone; hint?: string }

export function buildExecutionSteps(log: LogItem): ExecutionStep[] {
  const meta = statusMeta(log.status)
  const steps: ExecutionStep[] = [
    {
      key: 'client',
      label: '客户端请求',
      detail: `${protocolLabel(log.protocol)} · ${log.stream ? '流式' : '非流式'}`,
      tone: 'mist',
      hint: protocolEntry(log.protocol),
    },
    {
      key: 'gateway',
      label: '网关鉴权',
      detail: log.api_key_name || '—',
      tone: 'mist',
      hint: '按 API Key 绑定上游账号',
    },
    {
      key: 'account',
      label: '上游账号',
      detail: accountText(log),
      tone: log.account_source === 'agent' ? 'info' : 'mist',
      hint: log.account_source === 'agent' ? '经网关代理接入' : '直连上游',
    },
    {
      key: 'upstream',
      label: '上游调用',
      detail: `HTTP ${log.http_status} · ${log.latency_ms}ms`,
      tone: log.http_status >= 400 ? 'bad' : 'mist',
      hint: log.model || undefined,
    },
    {
      key: 'result',
      label: '返回结果',
      detail: `${meta.label} · ${messageCountText(log)}`,
      tone: meta.tone,
      hint: log.error_message || undefined,
    },
  ]
  return steps
}

export function messageCountText(log: Pick<LogItem, 'turn_count' | 'message_count' | 'total_tokens' | 'prompt_tokens' | 'completion_tokens'>) {
  const turns = log.turn_count ?? 0
  const parts: string[] = [`${turns} 轮调用`]
  const tokens = log.total_tokens ?? (log.prompt_tokens ?? 0) + (log.completion_tokens ?? 0)
  if (tokens) parts.push(formatTokensCompact(tokens))
  return parts.join(' · ')
}

function formatTokensCompact(value: number) {
  if (value < 1000) return `${value} Token`
  if (value < 1_000_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}K Token`
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M Token`
}

export function formatLogTime(value: string | null | undefined) {
  return formatTime(value)
}

export function formatClock(value: string | null | undefined) {
  const full = formatTime(value)
  if (full === '—') return full
  const parts = full.split(' ')
  return parts[1] || full
}

export type TurnSummary = {
  key: string
  index: number | null
  anchorSeq: number
  role: string
  preview: string
  textLength: number
  toolCount: number
  reasoningCount: number
}

export function summarizeTurn(turn: ConversationTurn): TurnSummary {
  const first = turn.messages[0]
  const text = turn.messages
    .map((message) => message.text)
    .filter(Boolean)
    .join('\n')
    .trim()
  const preview = text ? text.split('\n')[0].slice(0, 120) : '（空）'
  const toolCount = turn.messages.reduce((sum, message) => sum + message.toolCalls.length, 0)
  const reasoningCount = turn.messages.reduce((sum, message) => sum + message.reasoning.length, 0)
  return {
    key: turn.key,
    index: turn.index,
    anchorSeq: first?.seq ?? 0,
    role: first?.role ?? 'user',
    preview,
    textLength: text.length,
    toolCount,
    reasoningCount,
  }
}
