import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { LogContent } from '../components/LogContent'
import { LogConversation } from '../components/LogConversation'
import { LogTrajectory } from '../components/LogTrajectory'
import { MetaItem } from '../components/LogsShared'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { accountText, formatLogTime, messageCountText, protocolLabel, statusMeta } from '../lib/logView'
import { cn, formatTokenCount } from '../lib/utils'

type TabKey = 'conversation' | 'trajectory' | 'content'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'conversation', label: '摘要' },
  { key: 'trajectory', label: '追踪' },
  { key: 'content', label: '内容' },
]

function parseHighlight(raw: string | null): { start: number; end: number } | null {
  if (!raw) return null
  const [left, right] = raw.split('-')
  const start = Number(left)
  const end = Number(right)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  return { start, end }
}

function parseTargetSeq(raw: string | null): number | null {
  if (raw == null || raw === '') return null
  const value = Number(raw)
  if (!Number.isFinite(value) || value < 0) return null
  return value
}

export function LogDetailPage() {
  const params = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const id = Number(params.id)
  const ready = Number.isFinite(id)
  const [tab, setTab] = useState<TabKey>('conversation')
  const [pinned, setPinned] = useState<{ seq: number | null; highlight: { start: number; end: number } | null }>({
    seq: null,
    highlight: null,
  })

  useEffect(() => {
    const seq = parseTargetSeq(searchParams.get('seq'))
    setPinned({ seq, highlight: parseHighlight(searchParams.get('hl')) })
    if (seq != null) setTab('conversation')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  const { data, isPending, isFetching, refetch } = useQuery({
    queryKey: ['log', id],
    queryFn: () => api.log(id, false),
    enabled: ready,
  })
  const { data: messageData, isPending: messagesPending } = useQuery({
    queryKey: ['log-messages-all', id],
    queryFn: () => api.logMessages(id, 1, 1000),
    enabled: ready,
  })

  const messages = messageData?.items ?? []

  function stripAuditParams() {
    if (searchParams.has('seq') || searchParams.has('hl')) {
      const next = new URLSearchParams(searchParams)
      next.delete('seq')
      next.delete('hl')
      setSearchParams(next, { replace: true })
    }
  }

  if (isPending || !data || data.id !== id) {
    return <div className="text-mist">加载中…</div>
  }

  const meta = statusMeta(data.status)
  const totalTokens = data.total_tokens ?? (data.prompt_tokens ?? 0) + (data.completion_tokens ?? 0)

  return (
    <div className="min-w-0 max-w-full space-y-5 overflow-x-hidden">
      <div className="flex items-center justify-between gap-3">
        <Link to="/logs" className="inline-flex items-center gap-1 text-sm text-mist hover:text-signal">
          <ArrowLeft size={14} />
          返回列表
        </Link>
        <Button variant="line" onClick={() => void refetch()}>
          <RefreshCw size={14} className={cn(isFetching && 'animate-spin')} />
          刷新
        </Button>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="min-w-0 truncate text-xl font-semibold lg:text-2xl" title={data.model || undefined}>
              {data.model || '未命名会话'}
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist">
              <Badge tone="mist">{protocolLabel(data.protocol)}</Badge>
              <span>{data.stream ? '流式' : '非流式'}</span>
              <span className="text-mist/50">·</span>
              <span>会话 #{data.id}</span>
            </div>
          </div>
          <Badge tone={meta.tone} className="shrink-0">
            {meta.label}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm lg:grid-cols-4">
          <MetaItem label="Key">{data.api_key_name || '—'}</MetaItem>
          <MetaItem label="账号">{accountText(data)}</MetaItem>
          <MetaItem label="开始">{formatLogTime(data.created_at)}</MetaItem>
          <MetaItem label="最近活动">{formatLogTime(data.updated_at || data.created_at)}</MetaItem>
        </div>
      </div>

      {data.error_message ? (
        <Card className="break-words text-sm text-danger [overflow-wrap:anywhere]">{data.error_message}</Card>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-panel/60 px-4 py-3 text-sm">
        <span className="text-mist">
          轮次 <span className="font-medium text-paper tabular-nums">{data.turn_count ?? 0}</span>
        </span>
        <span className="text-mist">
          消息 <span className="font-medium text-paper tabular-nums">{data.message_count ?? 0}</span>
        </span>
        <span className="text-mist">
          输入 <span className="font-medium text-paper tabular-nums">{formatTokenCount(data.prompt_tokens)}</span>
        </span>
        <span className="text-mist">
          输出 <span className="font-medium text-paper tabular-nums">{formatTokenCount(data.completion_tokens)}</span>
        </span>
        <span className="text-mist">
          合计 <span className="font-medium text-paper tabular-nums">{formatTokenCount(totalTokens)}</span>
        </span>
        <span className="text-mist">
          耗时 <span className="font-medium text-paper tabular-nums">{data.latency_ms}ms</span>
        </span>
        <span className="text-mist">
          HTTP <span className="font-mono font-medium text-paper tabular-nums">{data.http_status}</span>
        </span>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={cn(
              'min-h-11 shrink-0 border-b-2 px-4 text-sm font-medium transition',
              tab === item.key ? 'border-signal text-signal' : 'border-transparent text-mist hover:text-paper',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {messagesPending ? (
        <div className="px-4 py-16 text-center text-sm text-mist">加载会话内容…</div>
      ) : tab === 'conversation' ? (
        <LogConversation
          log={data}
          messages={messages}
          targetSeq={pinned.seq}
          highlight={pinned.highlight}
          onLocated={stripAuditParams}
        />
      ) : tab === 'trajectory' ? (
        <LogTrajectory
          log={data}
          messages={messages}
          onJumpToSeq={(seq) => {
            setPinned({ seq, highlight: null })
            setTab('conversation')
          }}
        />
      ) : (
        <LogContent log={data} messages={messages} />
      )}

      <div className="text-right text-xs text-mist">{messageCountText(data)}</div>
    </div>
  )
}
