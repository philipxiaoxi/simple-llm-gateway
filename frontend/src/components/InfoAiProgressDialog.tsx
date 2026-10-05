import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type InfoItem } from '../lib/api'
import { relativeTime } from '../lib/info'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { Badge, Button, Dialog } from './ui'

export const INFO_AI_PROGRESS_KEY = ['info-ai-progress'] as const

const TABS = [
  { key: 'queue', label: '进行中', statuses: 'pending,processing' },
  { key: 'failed', label: '失败', statuses: 'failed' },
  { key: 'done', label: '已完成', statuses: 'done' },
  { key: 'skipped', label: '已跳过', statuses: 'skipped' },
  { key: 'all', label: '全部', statuses: '' },
] as const

type TabKey = (typeof TABS)[number]['key']

const STATUS_META: Record<string, { label: string; tone: 'ok' | 'bad' | 'warn' | 'mist' | 'info' }> = {
  pending: { label: '待判定', tone: 'mist' },
  processing: { label: '判定中', tone: 'info' },
  done: { label: '已完成', tone: 'ok' },
  failed: { label: '失败', tone: 'bad' },
  skipped: { label: '已跳过', tone: 'warn' },
}

function labelText(label: string) {
  if (label === 'ad') return '广告'
  if (label === 'valuable') return '高价值'
  if (label === 'general') return '常规'
  if (label === 'other') return '其他'
  return label
}

export function InfoAiProgressDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const location = useLocation()
  const [tab, setTab] = useState<TabKey>('queue')
  const active = TABS.find((item) => item.key === tab) ?? TABS[0]

  const stats = useQuery({
    queryKey: ['info-stats'],
    queryFn: api.infoStats,
    enabled: open,
    refetchInterval: (query) => ((query.state.data?.ai_pending ?? 0) > 0 ? 3000 : 0),
  })

  const items = useQuery({
    queryKey: [...INFO_AI_PROGRESS_KEY, active.statuses],
    queryFn: () =>
      api.infoItems({
        ai_status: active.statuses || undefined,
        include_hidden: 1,
        limit: 50,
        order: 'desc',
      }),
    enabled: open,
    refetchInterval: (query) => {
      const list = query.state.data?.items ?? []
      const busy = list.some((item) => item.ai_status === 'pending' || item.ai_status === 'processing')
      return busy ? 2000 : 0
    },
  })

  const rescore = useMutation({
    mutationFn: () => api.infoAiRescore({ scope: 'pending' }),
    onSuccess: async (result) => {
      notifyOk(`已重置 ${result.count} 条待判定`)
      await queryClient.invalidateQueries({ queryKey: ['info-stats'] })
      await queryClient.invalidateQueries({ queryKey: INFO_AI_PROGRESS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重新判定失败')),
  })

  const counts: Record<TabKey, number> = {
    queue: (stats.data?.ai_queued ?? 0) + (stats.data?.ai_processing ?? 0),
    failed: stats.data?.ai_failed ?? 0,
    done: stats.data?.ai_done ?? 0,
    skipped: stats.data?.ai_skipped ?? 0,
    all: stats.data?.item_count ?? 0,
  }
  const total = stats.data?.item_count ?? 0
  const finished = (stats.data?.ai_done ?? 0) + (stats.data?.ai_skipped ?? 0)
  const percent = total ? Math.round((finished / total) * 100) : 0
  const list = items.data?.items ?? []
  const canRetry = counts.failed + counts.skipped > 0

  return (
    <Dialog title="AI 判定进度" onClose={onClose} className="max-w-3xl">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
          <Sparkles size={14} className="text-signal" />
          <span>已完成 {stats.data?.ai_done ?? 0}</span>
          <span>· 待判定 {stats.data?.ai_queued ?? 0}</span>
          <span>· 判定中 {stats.data?.ai_processing ?? 0}</span>
          <span>· 失败 {stats.data?.ai_failed ?? 0}</span>
          <span>· 跳过 {stats.data?.ai_skipped ?? 0}</span>
        </div>

        <div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
            <div
              className="h-full rounded-full bg-signal transition-all"
              style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-mist">
            <span>整体进度 {percent}%</span>
            <span>{finished} / {total}</span>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={tab === item.key}
              onClick={() => setTab(item.key)}
              className={cn(
                'inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs transition',
                tab === item.key
                  ? 'border-signal/40 bg-signal/15 text-signal'
                  : 'border-line bg-white/[0.03] text-mist hover:text-paper',
              )}
            >
              {item.label}
              <span className="text-[10px] opacity-80">{counts[item.key]}</span>
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {canRetry ? (
              <Button
                type="button"
                variant="line"
                className="min-h-8 px-2.5 py-1 text-xs"
                disabled={rescore.isPending}
                onClick={() => rescore.mutate()}
              >
                <RefreshCw size={13} className={cn(rescore.isPending && 'animate-spin')} /> 重判失败/跳过
              </Button>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              className="min-h-8 px-2.5 py-1 text-xs"
              onClick={() => items.refetch()}
            >
              刷新
            </Button>
          </div>
        </div>

        <div className="max-h-[52vh] overflow-y-auto rounded-lg border border-line">
          {items.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-mist">
              <Loader2 size={15} className="animate-spin" /> 加载中…
            </div>
          ) : null}
          {items.isError ? (
            <div className="px-3 py-6 text-center text-sm text-danger">
              {errorMessage(items.error, '加载失败')}
            </div>
          ) : null}
          {!items.isLoading && !items.isError && list.length === 0 ? (
            <div className="px-3 py-10 text-center text-sm text-mist">该状态下暂无条目</div>
          ) : null}
          {list.map((item) => (
            <ProgressRow key={item.id} item={item} search={location.search} onOpen={onClose} />
          ))}
        </div>
        <p className="text-[11px] text-mist/80">判定进行中时列表会自动刷新；点击条目可查看详情。</p>
      </div>
    </Dialog>
  )
}

function ProgressRow({ item, search, onOpen }: { item: InfoItem; search: string; onOpen: () => void }) {
  const meta = STATUS_META[item.ai_status] ?? { label: item.ai_status, tone: 'mist' as const }
  const text = item.excerpt || item.text || '（无正文）'
  return (
    <Link
      to={`/info/${item.id}${search}`}
      onClick={onOpen}
      className="flex items-start gap-3 border-b border-line/70 px-3 py-2.5 transition last:border-0 hover:bg-white/[0.03]"
    >
      <div className="mt-0.5 flex w-16 shrink-0 flex-col items-start gap-1">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        {item.ai_score != null ? <span className="text-[11px] text-mist">{item.ai_score} 分</span> : null}
      </div>
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-sm text-paper">{text}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-mist">
          <span className="truncate">{item.source?.title || item.author_name || '未知来源'}</span>
          {item.ai_label ? <span>{labelText(item.ai_label)}</span> : null}
          {item.ai_scored_at ? <span>{relativeTime(item.ai_scored_at)}</span> : null}
        </div>
        {item.ai_error ? <div className="mt-1 line-clamp-1 text-[11px] text-danger">{item.ai_error}</div> : null}
      </div>
    </Link>
  )
}

export default InfoAiProgressDialog
