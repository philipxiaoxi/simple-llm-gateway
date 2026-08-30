import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Pagination } from '../components/Pagination'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { LOG_PAGE_SIZE, cn, formatTime } from '../lib/utils'

const COLLAPSE_CHAR_LIMIT = 400
const COLLAPSE_LINE_LIMIT = 8

function accountSourceLabel(source: 'upstream' | 'agent') {
  return source === 'agent' ? '[网关]' : '[上游]'
}

function statusLabel(status: string) {
  if (status === 'success') return '成功'
  if (status === 'error') return '失败'
  return status
}

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

function HighlightedText({
  text,
  start,
  end,
}: {
  text: string
  start: number | null
  end: number | null
}) {
  if (start == null || end == null || start < 0 || end <= start || start >= text.length) {
    return <>{text}</>
  }
  const safeEnd = Math.min(end, text.length)
  return (
    <>
      {text.slice(0, start)}
      <mark className="rounded-sm bg-warn/40 text-paper">{text.slice(start, safeEnd)}</mark>
      {text.slice(safeEnd)}
    </>
  )
}

function MessageBubble({
  role,
  content,
  expanded,
  onToggle,
  highlight,
  active,
}: {
  role: string
  content: unknown
  expanded: boolean
  onToggle: () => void
  highlight: { start: number; end: number } | null
  active: boolean
}) {
  const text = asText(content) || '（空）'
  const long = isLongText(text)
  const assistant = role === 'assistant'
  return (
    <div id={active ? 'audit-hit' : undefined} className={cn('min-w-0 max-w-full', assistant ? 'lg:pl-8' : 'lg:pr-8')}>
      <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">{role}</div>
      <div
        className={cn(
          'min-w-0 max-w-full overflow-hidden rounded-2xl p-4',
          assistant ? 'rounded-tl-sm border border-line bg-panel' : 'rounded-tr-sm bg-signal/10',
          active && 'ring-2 ring-warn/70',
        )}
      >
        {long && expanded ? (
          <button type="button" className="mb-3 text-xs text-signal hover:underline" onClick={onToggle}>
            收起
          </button>
        ) : null}
        <div
          className={cn(
            'max-w-full overflow-x-auto whitespace-pre-wrap break-all [overflow-wrap:anywhere]',
            long && !expanded && !highlight && 'line-clamp-8 overflow-hidden',
          )}
        >
          {highlight ? <HighlightedText text={text} start={highlight.start} end={highlight.end} /> : text}
        </div>
        {long ? (
          <button type="button" className="mt-3 text-xs text-signal hover:underline" onClick={onToggle}>
            {expanded ? '收起' : '展开全部'}
          </button>
        ) : null}
      </div>
    </div>
  )
}

function parseHighlight(raw: string | null): { start: number; end: number } | null {
  if (!raw) return null
  const [left, right] = raw.split('-')
  const start = Number(left)
  const end = Number(right)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  return { start, end }
}

export function LogDetailPage() {
  const params = useParams()
  const [searchParams] = useSearchParams()
  const id = Number(params.id)
  const ready = Number.isFinite(id)
  const targetSeq = Number(searchParams.get('seq'))
  const highlight = parseHighlight(searchParams.get('hl'))
  const hasTargetSeq = Number.isFinite(targetSeq) && targetSeq >= 0
  const [raw, setRaw] = useState(false)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [page, setPage] = useState(1)
  const [located, setLocated] = useState(false)
  const { data, isPending } = useQuery({
    queryKey: ['log', id],
    queryFn: () => api.log(id, false),
    enabled: ready,
  })
  const { data: pageData, isPending: pagePending } = useQuery({
    queryKey: ['log-messages', id, page, hasTargetSeq && !located ? targetSeq : null],
    queryFn: () => api.logMessages(id, page, LOG_PAGE_SIZE, hasTargetSeq && !located ? targetSeq : undefined),
    enabled: ready,
  })
  useEffect(() => {
    setExpanded({})
    setRaw(false)
    setPage(1)
    setLocated(false)
  }, [id, targetSeq])
  useEffect(() => {
    if (!hasTargetSeq || located || !pageData) return
    if (pageData.page !== page) setPage(pageData.page)
    setLocated(true)
  }, [hasTargetSeq, located, pageData, page])
  useEffect(() => {
    if (!hasTargetSeq) return
    const node = document.getElementById('audit-hit')
    node?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [hasTargetSeq, pageData])
  if (isPending || pagePending || !data || !pageData || data.id !== id) {
    return <div className="text-mist">加载中…</div>
  }
  const messages = pageData.items
  const total = pageData.total
  const pageCount = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE))
  const currentPage = pageData.page || page

  function goToPage(nextPage: number) {
    const bounded = Math.min(Math.max(nextPage, 1), pageCount)
    if (bounded === page) return
    setLocated(true)
    setPage(bounded)
    setExpanded({})
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="min-w-0 max-w-full space-y-5 overflow-x-hidden">
      <Link to="/logs" className="text-sm text-mist hover:text-signal">
        ← 返回列表
      </Link>
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h1 className="min-w-0 truncate text-2xl font-semibold">记录 #{data.id}</h1>
          <span className="shrink-0">
            <Badge tone={data.status === 'success' ? 'ok' : 'bad'}>{statusLabel(data.status)}</Badge>
          </span>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm lg:flex lg:flex-wrap lg:gap-x-6 lg:gap-y-2">
          <div className="min-w-0">
            <dt className="text-xs uppercase tracking-[0.16em] text-mist">Key</dt>
            <dd className="mt-1 truncate" title={data.api_key_name || undefined}>
              {data.api_key_name || '—'}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs uppercase tracking-[0.16em] text-mist">账号</dt>
            <dd className="mt-1 truncate" title={data.account_name ? `${accountSourceLabel(data.account_source)} ${data.account_name}` : undefined}>
              {data.account_name ? `${accountSourceLabel(data.account_source)} ${data.account_name}` : '—'}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs uppercase tracking-[0.16em] text-mist">时间</dt>
            <dd className="mt-1 truncate font-mono text-xs lg:text-sm">{formatTime(data.updated_at || data.created_at)}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs uppercase tracking-[0.16em] text-mist">模型</dt>
            <dd className="mt-1 truncate font-mono text-xs lg:text-sm" title={data.model || undefined}>
              {data.model || '—'}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs uppercase tracking-[0.16em] text-mist">耗时</dt>
            <dd className="mt-1 font-mono text-xs tabular-nums lg:text-sm">{data.latency_ms}ms</dd>
          </div>
        </dl>
      </div>
      {data.error_message ? (
        <Card className="break-all text-sm text-danger [overflow-wrap:anywhere]">{data.error_message}</Card>
      ) : null}
      <div className="min-w-0 space-y-3">
        {messages.map((message) => {
          const seq = message.seq
          const active = hasTargetSeq && seq === targetSeq
          return (
            <MessageBubble
              key={seq}
              role={message.role}
              content={message.content}
              expanded={Boolean(expanded[seq] || active)}
              onToggle={() =>
                setExpanded((current) => ({ ...current, [seq]: !current[seq] }))
              }
              highlight={active ? highlight : null}
              active={active}
            />
          )
        })}
      </div>
      <Pagination page={currentPage} pageCount={pageCount} total={total} unit="条消息" onPage={goToPage} />
      <Button variant="line" onClick={() => setRaw((value) => !value)}>
        {raw ? '收起原始 JSON' : '展开原始 JSON'}
      </Button>
      {raw ? (
        <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-all rounded-xl border border-line bg-ink p-4 font-mono text-xs text-mist [overflow-wrap:anywhere]">
          {JSON.stringify({ page: currentPage, total, messages }, null, 2)}
        </pre>
      ) : null}
    </div>
  )
}
