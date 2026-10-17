import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { LogContent } from '../components/LogContent'
import { LogConversation } from '../components/LogConversation'
import { LogTrajectory } from '../components/LogTrajectory'
import { LogTurnDialog } from '../components/LogTurnDialog'
import { MetaItem } from '../components/LogsShared'
import { Badge, Button, Card } from '../components/ui'
import { api, type LogMessageItem, type LogTurn } from '../lib/api'
import { accountText, formatLogTime, messageCountText, protocolLabel, statusMeta } from '../lib/logView'
import { cn, errorMessage, formatTokenCount } from '../lib/utils'
import { notifyBad } from '../lib/toast'

type TabKey = 'conversation' | 'trajectory' | 'content'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'conversation', label: '摘要' },
  { key: 'trajectory', label: '追踪' },
  { key: 'content', label: '内容' },
]

const MESSAGE_PAGE_SIZE = 50
// 轮次边界补齐时单次最多拉取的条数（一个轮次通常远小于该值）
const TURN_ALIGN_PAGE_SIZE = 200
// 超长轮次补齐的兜底次数上限，避免极端会话拉取过多
const TURN_ALIGN_MAX_ROUNDS = 3

export function LogDetailPage() {
  const params = useParams()
  const id = Number(params.id)
  const ready = Number.isFinite(id)
  const [tab, setTab] = useState<TabKey>('conversation')
  const [messages, setMessages] = useState<LogMessageItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [previewTurn, setPreviewTurn] = useState<{ anchor: number; to: number | null; index: number | null } | null>(null)
  const queryClient = useQueryClient()

  const { data, isPending, isFetching, refetch } = useQuery({
    queryKey: ['log', id],
    queryFn: () => api.log(id, false),
    enabled: ready,
  })

  const { data: turnsData } = useQuery({
    queryKey: ['log-turns', id],
    queryFn: () => api.logTurns(id),
    enabled: ready,
  })
  const turnsOutline = turnsData?.items ?? []

  // 取一批消息（toSeq 为游标：取该 seq 之前最新的 MESSAGE_PAGE_SIZE 条），
  // 若窗口最旧一条落在轮次中间，用轮次轮廓补齐到该轮次起点，避免出现“上下文片段”
  const loadWindow = useCallback(
    async (toSeq?: number): Promise<{ items: LogMessageItem[]; total: number }> => {
      const first = await api.logMessages(id, 1, MESSAGE_PAGE_SIZE, { toSeq })
      const items = [...first.items]
      const oldest = items[items.length - 1]
      if (oldest && oldest.role !== 'user') {
        const outline =
          queryClient.getQueryData<{ items: LogTurn[] }>(['log-turns', id])?.items ??
          (await api.logTurns(id)).items
        const parent = outline
          .filter((turn) => turn.anchor_seq <= oldest.seq && (turn.to_seq == null || oldest.seq <= turn.to_seq))
          .at(-1)
        if (parent && parent.anchor_seq < oldest.seq) {
          let cursorTo = oldest.seq - 1
          for (let round = 0; round < TURN_ALIGN_MAX_ROUNDS && cursorTo >= parent.anchor_seq; round += 1) {
            const head = await api.logMessages(id, 1, TURN_ALIGN_PAGE_SIZE, {
              fromSeq: parent.anchor_seq,
              toSeq: cursorTo,
            })
            items.push(...head.items)
            const minSeq = head.items.reduce((min, item) => Math.min(min, item.seq), Number.POSITIVE_INFINITY)
            if (head.items.length < TURN_ALIGN_PAGE_SIZE || minSeq <= parent.anchor_seq) break
            cursorTo = minSeq - 1
          }
        }
      }
      return { items, total: first.total }
    },
    [id, queryClient],
  )

  const mergeMessages = useCallback((previous: LogMessageItem[] | null, incoming: LogMessageItem[]) => {
    const merged = new Map<number, LogMessageItem>()
    for (const item of previous ?? []) merged.set(item.seq, item)
    for (const item of incoming) merged.set(item.seq, item)
    return [...merged.values()].sort((left, right) => right.seq - left.seq)
  }, [])

  useEffect(() => {
    if (!ready) return
    let cancelled = false
    setMessages(null)
    setTotal(0)
    setTab('conversation')
    setPreviewTurn(null)
    loadWindow()
      .then((payload) => {
        if (cancelled) return
        setMessages(payload.items)
        setTotal(payload.total)
      })
      .catch((caught) => {
        if (!cancelled) notifyBad(errorMessage(caught, '加载会话消息失败'))
      })
    return () => {
      cancelled = true
    }
  }, [id, ready, loadWindow])

  const loadOlder = () => {
    if (loadingOlder || !messages?.length) return
    const oldestSeq = messages.reduce((min, item) => Math.min(min, item.seq), Number.POSITIVE_INFINITY)
    setLoadingOlder(true)
    loadWindow(oldestSeq - 1)
      .then((payload) => {
        setMessages((previous) => mergeMessages(previous, payload.items))
      })
      .catch((caught) => notifyBad(errorMessage(caught, '加载更早消息失败')))
      .finally(() => setLoadingOlder(false))
  }

  const openTurnPreview = (anchor: number, to: number | null, index: number | null) => {
    setPreviewTurn({ anchor, to, index })
  }

  if (isPending || !data || data.id !== id) {
    return <div className="text-mist">加载中…</div>
  }

  const meta = statusMeta(data.status)
  const totalTokens = data.total_tokens ?? (data.prompt_tokens ?? 0) + (data.completion_tokens ?? 0)
  const loaded = messages ?? []
  const hasOlder = loaded.length > 0 && loaded.length < total
  const messagesReady = messages !== null

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

      {!messagesReady ? (
        <div className="px-4 py-16 text-center text-sm text-mist">加载会话内容…</div>
      ) : tab === 'conversation' ? (
        <LogConversation
          log={data}
          messages={loaded}
          turnsOutline={turnsOutline}
          onPreviewTurn={openTurnPreview}
          onLoadOlder={loadOlder}
          loadingOlder={loadingOlder}
          hasOlder={hasOlder}
        />
      ) : tab === 'trajectory' ? (
        <LogTrajectory
          log={data}
          messages={loaded}
          turnsOutline={turnsOutline}
          onPreviewTurn={openTurnPreview}
          onLoadOlder={loadOlder}
          loadingOlder={loadingOlder}
          hasOlder={hasOlder}
        />
      ) : (
        <LogContent log={data} messages={loaded} />
      )}

      <div className="text-right text-xs text-mist">
        {messageCountText(data)}
        {loaded.length < total ? ` · 已加载 ${loaded.length} / ${total} 条消息` : null}
      </div>

      {previewTurn ? (
        <LogTurnDialog
          log={data}
          anchor={previewTurn.anchor}
          to={previewTurn.to}
          index={previewTurn.index}
          onClose={() => setPreviewTurn(null)}
        />
      ) : null}
    </div>
  )
}
