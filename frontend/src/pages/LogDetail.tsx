import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { formatTime } from '../lib/utils'

type Message = { role: string; content: unknown }

function asText(content: unknown): string {
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

function extractMessages(log: { request_body?: unknown; response_body?: unknown; protocol: string }): Message[] {
  const request = (log.request_body || {}) as Record<string, unknown>
  const response = (log.response_body || {}) as Record<string, unknown>
  const messages: Message[] = []
  const rawMessages = request.messages
  if (Array.isArray(rawMessages)) {
    rawMessages.forEach((item) => {
      if (item && typeof item === 'object' && 'role' in item) {
        messages.push({ role: String((item as Message).role), content: (item as Message).content })
      }
    })
  } else if (typeof request.input === 'string') {
    messages.push({ role: 'user', content: request.input })
  }
  if (log.protocol === 'anthropic_messages') {
    const content = response.content
    messages.push({ role: 'assistant', content })
  } else if (log.protocol === 'openai_responses') {
    messages.push({ role: 'assistant', content: response.output_text || response.output })
  } else {
    const choices = response.choices as Array<{ message?: Message }> | undefined
    const message = choices?.[0]?.message
    if (message) messages.push({ role: 'assistant', content: message.content })
  }
  return messages
}

export function LogDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const { data } = useQuery({ queryKey: ['log', id], queryFn: () => api.log(id), enabled: Number.isFinite(id) })
  const [raw, setRaw] = useState(false)
  if (!data) return <div className="text-mist">加载中…</div>
  const messages = extractMessages(data)

  return (
    <div className="space-y-5">
      <Link to="/logs" className="text-sm text-mist hover:text-signal">
        ← 返回列表
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">记录 #{data.id}</h1>
          <div className="mt-1 text-sm text-mist">
            {formatTime(data.created_at)} · {data.model} · {data.latency_ms}ms
          </div>
        </div>
        <Badge tone={data.status === 'success' ? 'ok' : 'bad'}>{data.status}</Badge>
      </div>
      {data.error_message ? <Card className="text-sm text-danger">{data.error_message}</Card> : null}
      <div className="space-y-3">
        {messages.map((message, index) => (
          <div key={index} className={message.role === 'assistant' ? 'md:pl-8' : 'md:pr-8'}>
            <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">{message.role}</div>
            <div
              className={
                message.role === 'assistant'
                  ? 'rounded-2xl rounded-tl-sm border border-line bg-panel p-4 whitespace-pre-wrap'
                  : 'rounded-2xl rounded-tr-sm bg-signal/10 p-4 whitespace-pre-wrap'
              }
            >
              {asText(message.content) || '（空）'}
            </div>
          </div>
        ))}
      </div>
      <Button variant="line" onClick={() => setRaw((value) => !value)}>
        {raw ? '收起原始 JSON' : '展开原始 JSON'}
      </Button>
      {raw ? (
        <pre className="overflow-auto rounded-xl border border-line bg-ink p-4 font-mono text-xs text-mist">
          {JSON.stringify({ request: data.request_body, response: data.response_body }, null, 2)}
        </pre>
      ) : null}
    </div>
  )
}
