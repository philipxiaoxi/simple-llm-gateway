import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { cn, formatTime } from '../lib/utils'

const COLLAPSE_CHAR_LIMIT = 400
const COLLAPSE_LINE_LIMIT = 8
const PAGE_SIZE = 20

function isLongText(text: string): boolean {
  return text.length > COLLAPSE_CHAR_LIMIT || text.split('\n').length > COLLAPSE_LINE_LIMIT
}

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

function MessageBubble({
  role,
  content,
  expanded,
  onToggle,
}: {
  role: string
  content: unknown
  expanded: boolean
  onToggle: () => void
}) {
  const text = asText(content) || '（空）'
  const long = isLongText(text)
  const assistant = role === 'assistant'
  return (
    <div className={assistant ? 'md:pl-8' : 'md:pr-8'}>
      <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">{role}</div>
      <div
        className={
          assistant
            ? 'rounded-2xl rounded-tl-sm border border-line bg-panel p-4'
            : 'rounded-2xl rounded-tr-sm bg-signal/10 p-4'
        }
      >
        {long && expanded ? (
          <button type="button" className="mb-3 text-xs text-signal hover:underline" onClick={onToggle}>
            收起
          </button>
        ) : null}
        <div className={cn('whitespace-pre-wrap', long && !expanded && 'line-clamp-8')}>{text}</div>
        {long ? (
          <button type="button" className="mt-3 text-xs text-signal hover:underline" onClick={onToggle}>
            {expanded ? '收起' : '展开全部'}
          </button>
        ) : null}
      </div>
    </div>
  )
}

function MessagePager({
  total,
  page,
  pageCount,
  onPage,
}: {
  total: number
  page: number
  pageCount: number
  onPage: (page: number) => void
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm text-mist">
        共 {total} 条消息 · 第 {page} / {pageCount} 页
      </div>
      <div className="flex gap-2">
        <Button type="button" variant="line" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          上一页
        </Button>
        <Button type="button" variant="line" disabled={page >= pageCount} onClick={() => onPage(page + 1)}>
          下一页
        </Button>
      </div>
    </div>
  )
}

export function LogDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const ready = Number.isFinite(id)
  const [raw, setRaw] = useState(false)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [page, setPage] = useState(1)
  const { data } = useQuery({
    queryKey: ['log', id],
    queryFn: () => api.log(id, false),
    enabled: ready,
  })
  const { data: pageData } = useQuery({
    queryKey: ['log-messages', id, page],
    queryFn: () => api.logMessages(id, page, PAGE_SIZE),
    enabled: ready,
  })
  useEffect(() => {
    setExpanded({})
    setRaw(false)
    setPage(1)
  }, [id])
  if (!data || !pageData) return <div className="text-mist">加载中…</div>
  const messages = pageData.items
  const total = pageData.total
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.min(Math.max(page, 1), pageCount)
  const start = (currentPage - 1) * PAGE_SIZE

  function goToPage(nextPage: number) {
    setPage(nextPage)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="space-y-5">
      <Link to="/logs" className="text-sm text-mist hover:text-signal">
        ← 返回列表
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">记录 #{data.id}</h1>
          <div className="mt-1 text-sm text-mist">
            {data.api_key_name || '—'} · {data.account_name || '—'} · {formatTime(data.updated_at || data.created_at)} ·{' '}
            {data.model} · {data.latency_ms}ms
          </div>
        </div>
        <Badge tone={data.status === 'success' ? 'ok' : 'bad'}>{data.status}</Badge>
      </div>
      {data.error_message ? <Card className="text-sm text-danger">{data.error_message}</Card> : null}
      <MessagePager total={total} page={currentPage} pageCount={pageCount} onPage={goToPage} />
      <div className="space-y-3">
        {messages.map((message, offset) => {
          const index = start + offset
          return (
            <MessageBubble
              key={index}
              role={message.role}
              content={message.content}
              expanded={Boolean(expanded[index])}
              onToggle={() =>
                setExpanded((current) => ({ ...current, [index]: !current[index] }))
              }
            />
          )
        })}
      </div>
      <MessagePager total={total} page={currentPage} pageCount={pageCount} onPage={goToPage} />
      <Button variant="line" onClick={() => setRaw((value) => !value)}>
        {raw ? '收起原始 JSON' : '展开原始 JSON'}
      </Button>
      {raw ? (
        <pre className="overflow-auto rounded-xl border border-line bg-ink p-4 font-mono text-xs text-mist">
          {JSON.stringify({ page: currentPage, total, messages }, null, 2)}
        </pre>
      ) : null}
    </div>
  )
}
