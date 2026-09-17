import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { RotateCcw, Search, Trash2, X } from 'lucide-react'
import { Button, Card, Field, Input } from '../components/ui'
import { api, type McpKnowledgeJob } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  succeeded: '已完成',
  failed: '失败',
  canceled: '已取消',
}

const STATUS_TONES: Record<string, string> = {
  queued: 'border-line text-mist',
  running: 'border-signal/50 text-signal',
  succeeded: 'border-ok/50 text-ok',
  failed: 'border-danger/50 text-danger',
  canceled: 'border-warn/50 text-warn',
}

const KIND_LABELS: Record<string, string> = {
  ingest: '入库',
  reembed: '重新向量化',
}

const STATUS_FILTERS = [
  { value: '', label: '全部' },
  { value: 'queued,running', label: '进行中' },
  { value: 'failed', label: '失败' },
  { value: 'succeeded', label: '已完成' },
]

function formatSize(size: number) {
  if (!size) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

export function McpKnowledgeJobsPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const kbId = params.get('kb_id') || ''
  const keyword = params.get('q') || ''
  const [search, setSearch] = useState(keyword)

  useEffect(() => {
    setSearch(keyword)
  }, [keyword])

  const bases = useQuery({ queryKey: ['mcp-knowledge-bases'], queryFn: () => api.mcpKnowledgeBases() })

  const jobs = useQuery({
    queryKey: ['mcp-knowledge-jobs', status, kbId, keyword],
    queryFn: () => api.mcpKnowledgeJobs({ status, kb_id: kbId, q: keyword, limit: 100 }),
    refetchInterval: (query) => {
      const data = query.state.data
      const active = data?.items?.some((item) => item.status === 'queued' || item.status === 'running')
      return active ? 2000 : 10000
    },
  })

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-jobs'] })
    await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
    await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-docs'] })
  }

  const retry = useMutation({
    mutationFn: (id: number) => api.retryMcpKnowledgeJob(id),
    onSuccess: async () => {
      notifyOk('已重新排队')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重试失败')),
  })

  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelMcpKnowledgeJob(id),
    onSuccess: async () => {
      notifyOk('已取消')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '取消失败')),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteMcpKnowledgeJob(id),
    onSuccess: async () => {
      notifyOk('任务已删除')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const items = jobs.data?.items ?? []
  const counts = jobs.data?.counts ?? {}
  const activeCount = (counts.queued || 0) + (counts.running || 0)

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza/knowledge" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回知识库
      </Link>
      <div>
        <h2 className="text-xl font-semibold">采集任务</h2>
        <p className="mt-1 text-sm text-mist">
          入库与重新向量化在后台队列执行，可离开页面；失败任务可重试。
          {activeCount ? ` 当前进行中 ${activeCount} 个。` : ''}
        </p>
      </div>

      <Card className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
        <Field label="状态">
          <div className="flex flex-wrap gap-2">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.value || 'all'}
                type="button"
                onClick={() => updateParam('status', item.value)}
                className={cn(
                  'rounded-md border px-3 py-1.5 text-sm',
                  status === item.value ? 'border-signal/50 bg-signal/10 text-signal' : 'border-line text-mist',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="知识库">
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
            value={kbId}
            onChange={(e) => updateParam('kb_id', e.target.value)}
          >
            <option value="">全部知识库</option>
            {(bases.data || []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="搜索来源名">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
              <Input
                className="pl-9"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') updateParam('q', search.trim())
                }}
                placeholder="文件名 / 来源名"
              />
            </div>
            <Button type="button" variant="line" onClick={() => updateParam('q', search.trim())}>
              搜索
            </Button>
          </div>
        </Field>
      </Card>

      {jobs.isError ? <div className="text-sm text-danger">{errorMessage(jobs.error, '加载失败')}</div> : null}

      <div className="space-y-2">
        {items.map((job) => (
          <JobRow
            key={job.id}
            job={job}
            onRetry={() => retry.mutate(job.id)}
            onCancel={() => cancel.mutate(job.id)}
            onDelete={() => {
              if (window.confirm(`删除任务 #${job.id}「${job.source_name}」？（不会删除已入库文档）`)) {
                remove.mutate(job.id)
              }
            }}
            pending={retry.isPending || cancel.isPending || remove.isPending}
          />
        ))}
        {!items.length && !jobs.isFetching ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
            没有符合条件的任务
          </div>
        ) : null}
      </div>
    </div>
  )
}

function JobRow({
  job,
  onRetry,
  onCancel,
  onDelete,
  pending,
}: {
  job: McpKnowledgeJob
  onRetry: () => void
  onCancel: () => void
  onDelete: () => void
  pending: boolean
}) {
  const active = job.status === 'queued' || job.status === 'running'
  const failed = job.status === 'failed'
  const canRetry = (failed || job.status === 'canceled') && job.attempts < job.max_attempts
  return (
    <Card className="min-w-0 space-y-2 overflow-hidden p-3">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="break-all text-sm text-paper [overflow-wrap:anywhere]">{job.source_name}</span>
            <span className={cn('rounded-full border px-2 py-0.5 text-xs', STATUS_TONES[job.status] || 'border-line text-mist')}>
              {STATUS_LABELS[job.status] || job.status}
            </span>
            <span className="rounded-full border border-line px-2 py-0.5 text-xs text-mist">
              {KIND_LABELS[job.kind] || job.kind}
            </span>
          </div>
          <div className="mt-1 break-words text-xs text-mist [overflow-wrap:anywhere]">
            #{job.id} · {job.kb_name || job.kb_id} · {formatSize(job.content_size)}
            {job.chunk_count ? ` · ${job.chunk_count} 块` : ''}
            {job.total_chunks ? ` · 进度 ${job.processed_chunks}/${job.total_chunks}` : ''}
            {job.attempts ? ` · 第 ${job.attempts} 次` : ''}
            <br />
            创建 {formatTime(job.created_at)}
            {job.finished_at ? ` · 结束 ${formatTime(job.finished_at)}` : ''}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          {canRetry ? (
            <Button type="button" variant="line" disabled={pending} onClick={onRetry}>
              <RotateCcw size={14} /> 重试
            </Button>
          ) : null}
          {job.status === 'queued' ? (
            <Button type="button" variant="ghost" disabled={pending} onClick={onCancel}>
              <X size={14} /> 取消
            </Button>
          ) : null}
          {!active ? (
            <Button type="button" variant="ghost" disabled={pending} onClick={onDelete}>
              <Trash2 size={14} />
            </Button>
          ) : null}
        </div>
      </div>

      {active || failed ? (
        <div className="min-w-0 rounded-md border border-line bg-ink/60 p-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={failed ? 'text-danger' : 'text-mist'}>{job.message || '处理中…'}</span>
            <span className="shrink-0 tabular-nums text-mist">{job.percent}%</span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
            <div
              className={cn('h-full rounded-full transition-all', failed ? 'bg-danger' : 'bg-signal')}
              style={{ width: `${Math.max(2, Math.min(100, job.percent))}%` }}
            />
          </div>
          {job.error_message ? (
            <div className="mt-2 break-words text-xs text-danger [overflow-wrap:anywhere]">{job.error_message}</div>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

export default McpKnowledgeJobsPage
