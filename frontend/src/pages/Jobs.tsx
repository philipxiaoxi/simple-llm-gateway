import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api, type JobParam, type ScheduledJob } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

function statusTone(job: ScheduledJob): 'ok' | 'bad' | 'warn' | 'mist' {
  if (job.running) return 'warn'
  if (job.last_ok === false || job.error_message) return 'bad'
  if (job.last_ok) return 'ok'
  return 'mist'
}

function statusLabel(job: ScheduledJob) {
  if (job.running) return '运行中'
  if (job.last_ok === false) return '失败'
  if (job.error_message) return '异常'
  if (job.last_ok) return '正常'
  return '未运行'
}

function kindLabel(kind: string) {
  return kind === 'loop' ? '循环' : '按需'
}

function detailText(value: unknown) {
  if (value == null || value === '') return '—'
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)) return formatTime(value)
  return String(value)
}

const DETAIL_LABELS: Record<string, string> = {
  model_count: '模型数',
  due_count: '待刷新',
  account_count: '账号数',
  oldest_quota_at: '最早额度',
  newest_quota_at: '最近额度',
  token_count: 'Token 数',
  earliest_expires_at: '最早过期',
  stale: '已过期',
  total: '条目数',
  processed: '本轮处理',
  new_findings: '新增命中',
  remaining: '待扫记录',
  lexicon_ok: '词库就绪',
  scanned_count: '已扫记录',
  total_logs: '请求总数',
  finding_count: '命中总数',
}

function JobCard({ job }: { job: ScheduledJob }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Record<string, string>>({})

  const paramSignature = job.params.map((param) => `${param.key}:${param.value}`).join('|')
  useEffect(() => {
    const next: Record<string, string> = {}
    for (const param of job.params) next[param.key] = String(param.value)
    setDraft(next)
  }, [job.id, paramSignature])

  const runMutation = useMutation({
    mutationFn: () => api.runJob(job.id),
    onSuccess: (payload) => {
      queryClient.setQueryData(['jobs'], payload)
      const current = payload.items.find((item) => item.id === job.id)
      if (current?.error_message) notifyBad(current.error_message)
      else notifyOk(current?.last_message || '已执行')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '执行失败')),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, number> = {}
      for (const param of job.params) {
        const raw = draft[param.key]
        const value = Number(raw)
        if (!Number.isFinite(value)) throw new Error(`${param.label} 必须是数字`)
        payload[param.key] = value
      }
      return api.updateJob(job.id, payload)
    },
    onSuccess: (payload) => {
      queryClient.setQueryData(['jobs'], payload)
      notifyOk('参数已保存')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const dirty = job.params.some((param) => String(param.value) !== (draft[param.key] ?? ''))

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{job.name}</h2>
            <Badge tone={statusTone(job)}>{statusLabel(job)}</Badge>
            <Badge tone="mist">{kindLabel(job.kind)}</Badge>
          </div>
          <p className="mt-1 text-sm text-mist">{job.description}</p>
        </div>
        <Button
          type="button"
          variant="line"
          className="shrink-0"
          disabled={job.running || runMutation.isPending}
          onClick={() => runMutation.mutate()}
        >
          <RefreshCw size={16} className={job.running || runMutation.isPending ? 'animate-spin' : undefined} />
          立即请求
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Meta label="上次执行" value={formatTime(job.last_finished_at)} />
        <Meta label="下次循环" value={job.kind === 'loop' ? formatTime(job.next_run_at) : '手动请求'} />
        <Meta label="缓存时间" value={formatTime(job.cache_fetched_at)} />
        <Meta label="缓存到期" value={formatTime(job.cache_expires_at)} />
      </div>

      {job.last_message || job.error_message ? (
        <div className={cn('text-sm', job.error_message ? 'text-danger' : 'text-mist')}>
          {job.error_message || job.last_message}
        </div>
      ) : null}

      {job.source_url ? (
        <a
          href={job.source_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit items-center gap-1 text-xs text-signal hover:underline"
        >
          <ExternalLink size={12} />
          {job.source_url}
        </a>
      ) : null}

      {Object.keys(job.details).length ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist">
          {Object.entries(job.details).map(([key, value]) => (
            <span key={key}>
              {DETAIL_LABELS[key] || key} {typeof value === 'boolean' ? (value ? '是' : '否') : detailText(value)}
            </span>
          ))}
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="grid gap-3 sm:grid-cols-2">
          {job.params.map((param) => (
            <ParamField
              key={param.key}
              param={param}
              value={draft[param.key] ?? String(param.value)}
              onChange={(next) => setDraft((current) => ({ ...current, [param.key]: next }))}
            />
          ))}
        </div>
        <Button
          type="button"
          className="w-full lg:w-auto"
          disabled={!dirty || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          保存参数
        </Button>
      </div>
    </Card>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-ink/40 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">{label}</div>
      <div className="mt-1 truncate font-mono text-xs tabular-nums text-paper" title={value}>
        {value}
      </div>
    </div>
  )
}

function ParamField({
  param,
  value,
  onChange,
}: {
  param: JobParam
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Field label={`${param.label} · ${param.hint}`}>
      <Input
        type="number"
        min={param.min}
        max={param.max}
        step={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

export function JobsPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['jobs'],
    queryFn: api.jobs,
  })
  const jobs = data?.items ?? []

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BACKGROUND JOBS</div>
          <h1 className="text-2xl font-semibold">定时任务</h1>
          <p className="mt-1 text-sm text-mist">查看循环任务、缓存时间和上次结果，改间隔后保存，或立刻请求一次。</p>
        </div>
        <Button type="button" variant="line" className="shrink-0" disabled={isFetching} onClick={() => void refetch()}>
          <RefreshCw size={16} className={isFetching ? 'animate-spin' : undefined} />
          刷新状态
        </Button>
      </div>

      {isLoading ? <div className="text-sm text-mist">读取任务状态…</div> : null}
      {isError ? <div className="text-sm text-danger">{errorMessage(error, '读取失败')}</div> : null}

      <div className="space-y-4">
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>
    </div>
  )
}
