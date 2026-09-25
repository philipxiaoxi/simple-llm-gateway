import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, Search, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button, Card, Field, Input } from '../components/ui'
import { api, getToken, type DocParseJob } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

const ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.html,.htm'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '转换中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const STATUS_TONES: Record<string, string> = {
  queued: 'border-line text-mist',
  running: 'border-signal/50 text-signal',
  succeeded: 'border-ok/50 text-ok',
  failed: 'border-danger/50 text-danger',
  cancelled: 'border-warn/50 text-warn',
}

const STATUS_FILTERS = [
  { value: '', label: '全部' },
  { value: 'queued,running', label: '进行中' },
  { value: 'failed', label: '失败' },
  { value: 'succeeded', label: '已完成' },
]

type UploadItem = {
  name: string
  size: number
  percent: number
  status: 'uploading' | 'failed'
  message: string
}

function formatSize(size: number) {
  if (!size) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

export function McpDocParsePage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const keyword = params.get('q') || ''
  const [search, setSearch] = useState(keyword)
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [kbId, setKbId] = useState('')

  useEffect(() => {
    setSearch(keyword)
  }, [keyword])

  const jobs = useQuery({
    queryKey: ['docparse-jobs', status, keyword],
    queryFn: () => api.docparseJobs({ status, q: keyword }),
    refetchInterval: (query) => {
      const active = query.state.data?.items?.some((item) => item.status === 'queued' || item.status === 'running')
      return active ? 2000 : 10000
    },
  })
  const bases = useQuery({ queryKey: ['mcp-knowledge-bases'], queryFn: () => api.mcpKnowledgeBases() })
  const preview = (jobs.data?.items || []).find((item) => item.job_id === previewId) || null

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['docparse-jobs'] })
  }

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) {
        setUploads((current) => [
          { name: file.name, size: file.size, percent: 0, status: 'uploading', message: '上传中' },
          ...current.filter((item) => item.name !== file.name),
        ])
        try {
          const job = await api.createDocParseJob(file, (percent) => {
            setUploads((current) =>
              current.map((item) => (item.name === file.name ? { ...item, percent, message: '上传中' } : item)),
            )
          })
          setUploads((current) => current.filter((item) => item.name !== file.name))
          setPreviewId(job.job_id)
          if (job.status === 'failed') notifyBad(job.error_message || `${file.name} 转换失败`)
        } catch (caught) {
          const message = errorMessage(caught, '上传失败')
          setUploads((current) =>
            current.map((item) => (item.name === file.name ? { ...item, status: 'failed', message } : item)),
          )
          notifyBad(message)
        }
      }
    },
    onSettled: invalidate,
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteDocParseJob(id),
    onSuccess: async () => {
      notifyOk('任务已删除')
      setPreviewId(null)
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const retry = useMutation({
    mutationFn: (id: string) => api.retryDocParseJob(id),
    onSuccess: async () => {
      notifyOk('已重新转换')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重试失败')),
  })

  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelDocParseJob(id),
    onSuccess: async () => {
      notifyOk('已取消')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '取消失败')),
  })

  const ingest = useMutation({
    mutationFn: () => api.ingestDocParseJob(preview!.job_id, kbId),
    onSuccess: async (job) => {
      notifyOk(`已送入知识库，采集任务 ${job.ingest_job_id ?? ''}`)
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '送入失败')),
  })

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  async function download(job: DocParseJob) {
    const token = getToken()
    const response = await fetch(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(job.job_id)}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) {
      notifyBad('下载失败')
      return
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${job.source_name.replace(/\.[^.]+$/, '')}.md`
    link.click()
    URL.revokeObjectURL(url)
  }

  const items = jobs.data?.items ?? []
  const counts = jobs.data?.counts ?? {}
  const activeCount = (counts.queued || 0) + (counts.running || 0)

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>
      <div>
        <h2 className="text-xl font-semibold">转换任务</h2>
        <p className="mt-1 text-sm text-mist">
          Word、PDF、Excel、PPT、HTML 转 Markdown。上传后进入任务列表，可离开页面。
          {activeCount ? ` 当前进行中 ${activeCount} 个。` : ''}
        </p>
      </div>

      <Card className="grid gap-3 p-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:items-end">
        <Field label="上传文件（可多选）">
          <input
            type="file"
            accept={ACCEPT}
            multiple
            disabled={upload.isPending}
            className="block w-full min-w-0 text-sm text-mist file:mr-3 file:rounded-md file:border file:border-line file:bg-panel file:px-3 file:py-2 file:text-sm file:text-paper"
            onChange={(event) => {
              const files = Array.from(event.target.files || [])
              event.target.value = ''
              if (files.length) upload.mutate(files)
            }}
          />
        </Field>
        <p className="text-xs text-mist">单文件 30MB，PDF 最多 200 页。旧版 .doc/.xls/.ppt 需要部署环境安装 LibreOffice。</p>
      </Card>

      {uploads.length ? (
        <div className="space-y-2">
          {uploads.map((item) => (
            <Card key={item.name} className="min-w-0 space-y-2 overflow-hidden p-3">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 break-all text-paper">{item.name}</span>
                <span className="shrink-0 tabular-nums text-xs text-mist">{item.percent}%</span>
              </div>
              <div className="text-xs text-mist">
                {formatSize(item.size)} · {item.message}
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
                <div
                  className={cn('h-full rounded-full transition-all', item.status === 'failed' ? 'bg-danger' : 'bg-signal')}
                  style={{ width: `${Math.max(2, item.percent)}%` }}
                />
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      <Card className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-end">
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
        <Field label="搜索文件名">
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
              <Input
                className="pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') updateParam('q', search.trim())
                }}
                placeholder="文件名"
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
            key={job.job_id}
            job={job}
            pending={retry.isPending || cancel.isPending || remove.isPending}
            onPreview={() => setPreviewId(job.job_id === previewId ? null : job.job_id)}
            onRetry={() => retry.mutate(job.job_id)}
            onCancel={() => cancel.mutate(job.job_id)}
            onDownload={() => download(job)}
            onDelete={() => {
              if (window.confirm(`删除任务「${job.source_name}」？已送入知识库的文档不会删除。`)) remove.mutate(job.job_id)
            }}
          />
        ))}
        {!items.length && !jobs.isFetching ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
            没有符合条件的任务
          </div>
        ) : null}
      </div>

      {preview ? (
        <Card className="min-w-0 space-y-3 overflow-hidden p-4">
          <div className="text-sm font-medium text-paper">预览 · {preview.source_name}</div>
          {preview.warnings?.length ? (
            <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">{preview.warnings.join('；')}</div>
          ) : null}
          <pre className="max-h-[28rem] min-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-ink p-3 text-xs text-paper [overflow-wrap:anywhere]">
            {preview.markdown || '转换完成后在这里预览 Markdown。'}
          </pre>
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <Field label="送入知识库">
              <select
                className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
                value={kbId}
                onChange={(event) => setKbId(event.target.value)}
              >
                <option value="">选择知识库</option>
                {(bases.data || []).map((base) => (
                  <option key={base.id} value={base.id}>
                    {base.name}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              type="button"
              variant="line"
              disabled={!kbId || preview.status !== 'succeeded' || ingest.isPending}
              onClick={() => ingest.mutate()}
            >
              送入
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  )
}

function JobRow({
  job,
  pending,
  onPreview,
  onRetry,
  onCancel,
  onDownload,
  onDelete,
}: {
  job: DocParseJob
  pending: boolean
  onPreview: () => void
  onRetry: () => void
  onCancel: () => void
  onDownload: () => void
  onDelete: () => void
}) {
  const active = job.status === 'queued' || job.status === 'running'
  const failed = job.status === 'failed'
  return (
    <Card className="min-w-0 space-y-2 overflow-hidden p-3">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="break-all text-left text-sm text-paper [overflow-wrap:anywhere]" onClick={onPreview}>
              {job.source_name}
            </button>
            <span className={cn('rounded-full border px-2 py-0.5 text-xs', STATUS_TONES[job.status] || 'border-line text-mist')}>
              {STATUS_LABELS[job.status] || job.status}
            </span>
          </div>
          <div className="mt-1 break-words text-xs text-mist [overflow-wrap:anywhere]">
            {formatSize(job.source_size)}
            {job.page_count ? ` · ${job.page_count} 页` : ''}
            {job.ingest_job_id ? ` · 已送入采集 #${job.ingest_job_id}` : ''}
            <br />
            创建 {job.created_at ? formatTime(job.created_at) : '—'}
            {job.finished_at ? ` · 结束 ${formatTime(job.finished_at)}` : ''}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button type="button" variant="line" disabled={job.status !== 'succeeded'} onClick={onPreview}>
            预览
          </Button>
          <Button type="button" variant="line" disabled={!job.download_ready} onClick={onDownload}>
            下载
          </Button>
          {failed ? (
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

export default McpDocParsePage
