import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, ExternalLink, Play, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api, type DouyinMedia } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, elapsedSeconds, errorMessage, formatBytes, formatTime } from '../lib/utils'

const STATUS: Record<string, { label: string; tone: 'ok' | 'bad' | 'warn' | 'mist' | 'info' }> = {
  queued: { label: '排队中', tone: 'mist' },
  downloading: { label: '转存中', tone: 'info' },
  succeeded: { label: '已完成', tone: 'ok' },
  partial: { label: '部分完成', tone: 'warn' },
  failed: { label: '失败', tone: 'bad' },
}

const TERMINAL = new Set(['succeeded', 'partial', 'failed'])
const STAGE_ORDER = ['resolving', 'downloading', 'done'] as const
const STAGE_LABEL: Record<string, string> = { resolving: '解析中', downloading: '下载中', done: '完成' }

export function McpDouyinDetailPage() {
  const { jobId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['mcp-douyin-job', jobId],
    queryFn: () => api.mcpDouyinJob(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (state) => {
      const job = state.state.data
      return job && !TERMINAL.has(job.status) ? 1500 : false
    },
  })

  const retry = useMutation({
    mutationFn: () => api.retryMcpDouyinJob(jobId),
    onSuccess: async () => {
      notifyOk('已重新入队')
      await queryClient.invalidateQueries({ queryKey: ['mcp-douyin-job', jobId] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重试失败')),
  })

  const remove = useMutation({
    mutationFn: () => api.deleteMcpDouyinJob(jobId),
    onSuccess: async () => {
      notifyOk('任务已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-douyin'] })
      navigate('/mcp-plaza/douyin')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const job = query.data

  if (query.isLoading) {
    return <div className="text-sm text-mist">加载中…</div>
  }
  if (query.isError || !job) {
    return (
      <div className="space-y-3">
        <Link to="/mcp-plaza/douyin" className="inline-flex text-sm text-mist hover:text-paper">
          ← 返回列表
        </Link>
        <div className="text-sm text-danger">{errorMessage(query.error, '任务不存在')}</div>
      </div>
    )
  }

  const status = STATUS[job.status] || { label: job.status, tone: 'mist' as const }
  const media = job.media ?? []
  const downloadable = media.filter((item) => Boolean(item.download_url))
  const elapsed = TERMINAL.has(job.status) ? 0 : elapsedSeconds(job.created_at)

  function downloadAll() {
    downloadable.forEach((item, position) => {
      const url = mediaUrl(item)
      if (!url) return
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = item.filename || `douyin-${item.index_no}`
      document.body.appendChild(anchor)
      window.setTimeout(() => {
        anchor.click()
        anchor.remove()
      }, position * 300)
    })
  }

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza/douyin" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回列表
      </Link>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="break-words text-xl font-semibold">{job.title || job.aweme_id || '未命名作品'}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-mist">
            <Badge tone={status.tone}>{status.label}</Badge>
            <span>{job.author_name || '—'}</span>
            <span className="rounded-full border border-line px-2 py-0.5 text-xs">解析器 {job.extractor || '—'}</span>
            <span className="rounded-full border border-line px-2 py-0.5 text-xs">{job.rehost ? '已转存' : '仅解析'}</span>
          </div>
          <div className="mt-2 break-all text-xs text-mist [overflow-wrap:anywhere]">{job.source_url}</div>
        </div>
        <div className="flex shrink-0 gap-2">
          {downloadable.length ? (
            <Button type="button" onClick={downloadAll}>
              <Download size={15} /> 下载全部
            </Button>
          ) : null}
          {job.status === 'failed' || job.status === 'partial' ? (
            <Button type="button" variant="line" disabled={retry.isPending} onClick={() => retry.mutate()}>
              <RefreshCw size={15} /> 重试
            </Button>
          ) : null}
          <Button type="button" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
            <Trash2 size={15} /> 删除
          </Button>
        </div>
      </div>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          {STAGE_ORDER.map((stage, index) => {
            const currentIndex = Math.max(0, STAGE_ORDER.indexOf(job.stage as (typeof STAGE_ORDER)[number]))
            const failedStep = job.status === 'failed' && stage === 'done'
            const reached = index <= currentIndex
            const active = (index === currentIndex && !TERMINAL.has(job.status)) || failedStep
            const tone = failedStep
              ? 'border-danger/60 bg-danger/10 text-danger'
              : active
                ? 'border-signal/60 bg-signal/10 text-signal'
                : reached
                  ? 'border-line text-paper'
                  : 'border-line text-mist'
            const dot = failedStep ? 'bg-danger' : active ? 'bg-signal' : reached ? 'bg-mist' : 'bg-line'
            return (
              <div key={stage} className="flex items-center gap-2">
                <div className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${tone}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
                  {failedStep ? '失败' : STAGE_LABEL[stage] || stage}
                </div>
                {index < STAGE_ORDER.length - 1 ? <span className="text-mist">→</span> : null}
              </div>
            )
          })}
        </div>
        <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-mist">
          <span>{job.message || '处理中'}</span>
          <span className="flex flex-wrap gap-3">
            {job.stage === 'downloading' && job.expected_bytes ? (
              <span>
                已下载 {formatBytes(job.downloaded_bytes)} / {formatBytes(job.expected_bytes)}
              </span>
            ) : job.downloaded_bytes ? (
              <span>已下载 {formatBytes(job.downloaded_bytes)}</span>
            ) : null}
            {elapsed ? <span>已耗时 {elapsed}s</span> : null}
            <span>{job.percent}%</span>
          </span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
          <div
            className="h-full rounded-full bg-signal transition-all"
            style={{ width: `${Math.min(100, Math.max(2, job.percent))}%` }}
          />
        </div>
      </Card>

      {job.error_message ? (
        <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {job.error_message}
        </div>
      ) : null}

      <Card className="p-4">
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <Meta label="媒体项" value={`${job.success_count}/${job.media_count}`} />
          <Meta label="类型" value={job.kind === 'gallery' ? '图集' : '视频'} />
          <Meta label="总大小" value={job.total_bytes ? formatBytes(job.total_bytes) : '—'} />
          <Meta label="更新时间" value={job.finished_at ? formatTime(job.finished_at) : formatTime(job.created_at)} />
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {media.map((item) => (
          <MediaCard key={item.id} media={item} />
        ))}
      </div>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="text-xs uppercase tracking-[0.14em] text-mist">{label}</div>
      <div className="mt-1 truncate text-paper">{value}</div>
    </div>
  )
}

function mediaUrl(media: DouyinMedia): string | undefined {
  if (!media.download_url) return undefined
  if (/^https?:\/\//i.test(media.download_url)) return media.download_url
  return `${window.location.origin}${media.download_url}`
}

function MediaCard({ media }: { media: DouyinMedia }) {
  const [previewing, setPreviewing] = useState(false)
  const url = mediaUrl(media)
  const playable = media.status === 'ready' && Boolean(url)

  async function onCopy() {
    if (!url) return
    try {
      await copyText(url)
      notifyOk('下载地址已复制')
    } catch {
      notifyBad('复制失败')
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel">
      <div className="flex h-44 items-center justify-center bg-ink">
        {playable && previewing && media.kind === 'video' ? (
          <video controls playsInline preload="none" src={url} className="h-full w-full bg-black object-contain">
            <track kind="captions" />
          </video>
        ) : playable && previewing && media.kind === 'image' ? (
          <img src={url} alt="" className="h-full w-full object-contain" referrerPolicy="no-referrer" />
        ) : playable && previewing && media.kind === 'audio' ? (
          <audio controls preload="none" src={url} className="w-full px-3">
            <track kind="captions" />
          </audio>
        ) : playable ? (
          <button
            type="button"
            onClick={() => setPreviewing(true)}
            className="flex flex-col items-center gap-1 text-mist transition hover:text-paper"
          >
            <Play size={26} />
            <span className="text-xs">点击预览</span>
          </button>
        ) : (
          <span className="text-xs text-mist">{media.status === 'ready' ? '无预览' : media.status === 'failed' ? '转存失败' : '转存中'}</span>
        )}
      </div>
      <div className="space-y-2 p-3">
        <div className="flex items-center justify-between text-xs text-mist">
          <span>#{media.index_no} · {media.kind}</span>
          <span>{media.size_bytes ? formatBytes(media.size_bytes) : '—'}</span>
        </div>
        {media.error_message ? <div className="text-xs text-danger">{media.error_message}</div> : null}
        <div className="flex flex-wrap gap-2">
          {playable ? (
            <Button
              type="button"
              variant="line"
              className="min-h-9 px-2 py-1 text-xs md:min-h-8"
              onClick={() => setPreviewing((value) => !value)}
            >
              <Play size={14} /> {previewing ? '收起' : '预览'}
            </Button>
          ) : null}
          {url ? (
            <Button type="button" variant="line" className="min-h-9 px-2 py-1 text-xs md:min-h-8" onClick={onCopy}>
              <Copy size={14} /> 复制地址
            </Button>
          ) : null}
          {url ? (
            <a
              href={url}
              download={media.filename || `douyin-${media.index_no}`}
              className="inline-flex min-h-9 items-center gap-1 rounded-md border border-line bg-panel-2 px-2 py-1 text-xs text-paper hover:border-mist/40 md:min-h-8"
            >
              <Download size={14} /> 下载
            </a>
          ) : null}
          {media.original_url ? (
            <a
              href={media.original_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-9 items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-mist hover:text-paper md:min-h-8"
            >
              <ExternalLink size={14} /> 原始链接
            </a>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default McpDouyinDetailPage
