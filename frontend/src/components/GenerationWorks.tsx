import { Download, Film, Image as ImageIcon, Loader2, Maximize2, Play, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import type { GenerationTask } from '../lib/api'
import { formatTime, isTaskActive, resultThumb, statusLabel, statusTone, taskModelLabel, taskParamSummary } from '../lib/generation'
import { cn } from '../lib/utils'
import { Badge, Button } from './ui'

function ActionButton({
  title,
  onClick,
  children,
  tone = 'default',
}: {
  title: string
  onClick: () => void
  children: ReactNode
  tone?: 'default' | 'danger'
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-md bg-black/55 backdrop-blur transition',
        tone === 'danger' ? 'text-white hover:bg-danger' : 'text-white hover:bg-black/80',
      )}
    >
      {children}
    </button>
  )
}

function ImageLightbox({ url, alt, onClose }: { url: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const body = document.body
    const previousOverflow = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      body.style.overflow = previousOverflow
    }
  }, [onClose])

  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="大图预览" className="fixed inset-0 z-[60] flex items-center justify-center bg-black/85 p-4">
      <button type="button" aria-label="关闭预览" onClick={onClose} className="absolute inset-0 h-full w-full cursor-default" />
      <div className="absolute right-3 top-3 flex gap-2">
        <a
          href={url}
          download
          className="inline-flex h-9 items-center gap-1 rounded-md bg-white/10 px-3 text-xs text-white backdrop-blur transition hover:bg-white/20"
        >
          <Download size={14} /> 下载
        </a>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-9 items-center rounded-md bg-white/10 px-3 text-xs text-white backdrop-blur transition hover:bg-white/20"
        >
          关闭
        </button>
      </div>
      <img
        src={url}
        alt={alt}
        className="relative max-h-[88vh] max-w-[92vw] rounded-lg border border-line object-contain shadow-2xl"
      />
    </div>,
    document.body,
  )
}

function WorkMedia({ task, onPreview }: { task: GenerationTask; onPreview: (url: string, alt: string) => void }) {
  const thumb = resultThumb(task)
  const active = isTaskActive(task.status)
  const [playing, setPlaying] = useState(false)

  if (task.kind === 'video') {
    const url = thumb?.url ?? null
    if (active || !url) {
      return (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 bg-panel-2 text-mist">
          {active ? <Loader2 size={22} className="animate-spin" /> : <Film size={24} />}
          <span className="text-[11px] text-mist/70">{active ? '视频生成中' : '视频'}</span>
        </div>
      )
    }
    if (playing) {
      return (
        <video
          src={url}
          controls
          autoPlay
          playsInline
          preload="metadata"
          onEnded={() => setPlaying(false)}
          className="h-full w-full bg-black object-contain"
        >
          <track kind="captions" />
        </video>
      )
    }
    return (
      <button
        type="button"
        aria-label="播放视频"
        onClick={() => setPlaying(true)}
        className="group/play flex h-full w-full flex-col items-center justify-center gap-2 bg-panel-2 text-mist"
      >
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur transition duration-200 group-hover/play:scale-110 group-hover/play:bg-black/80">
          <Play size={22} className="translate-x-[1px] fill-current" />
        </span>
        <span className="text-[11px] text-mist/70 transition group-hover/play:text-paper">点击播放</span>
      </button>
    )
  }

  const fullUrl = thumb?.url
  const image = thumb?.thumb_url ?? fullUrl
  if (image && fullUrl) {
    return (
      <button
        type="button"
        aria-label="查看大图"
        onClick={() => onPreview(fullUrl, task.prompt)}
        className="group/img relative block h-full w-full overflow-hidden"
      >
        <img
          src={image}
          alt={task.prompt}
          loading="lazy"
          className="h-full w-full object-cover transition duration-300 group-hover/img:scale-[1.04]"
        />
        <span className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition group-hover/img:bg-black/20 group-hover/img:opacity-100">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-black/50 text-white backdrop-blur">
            <Maximize2 size={16} />
          </span>
        </span>
      </button>
    )
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-panel-2 text-mist">
      {active ? <Loader2 size={22} className="animate-spin" /> : <ImageIcon size={22} />}
    </div>
  )
}

export function GenerationWorks({
  tasks,
  onSelect,
  onDelete,
  onReuse,
  onCreate,
}: {
  tasks: GenerationTask[]
  onSelect: (task: GenerationTask) => void
  onDelete: (task: GenerationTask) => void
  onReuse: (task: GenerationTask) => void
  onCreate: () => void
}) {
  const [preview, setPreview] = useState<{ url: string; alt: string } | null>(null)

  if (!tasks.length) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line px-4 py-16 text-center">
        <ImageIcon size={26} className="text-mist" />
        <div className="text-sm text-mist">还没有作品</div>
        <div className="text-xs text-mist/70">点击下方按钮开始生成，结果会出现在这里。</div>
        <Button type="button" onClick={onCreate}>
          <Plus size={15} /> 开始生成
        </Button>
      </div>
    )
  }
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
        {tasks.map((task) => {
          const thumb = resultThumb(task)
          const canDownload = Boolean(thumb?.url)
          const active = isTaskActive(task.status)
          return (
            <div
              key={task.id}
              className="group relative overflow-hidden rounded-xl border border-line bg-panel/70 transition hover:border-signal/50 hover:shadow-[0_8px_30px_rgba(0,0,0,0.35)]"
            >
              <div className="relative aspect-square overflow-hidden bg-panel-2">
                <WorkMedia task={task} onPreview={(url, alt) => setPreview({ url, alt })} />
                <div className="pointer-events-none absolute left-2 top-2">
                  <Badge tone={statusTone(task.status)}>{statusLabel(task.status)}</Badge>
                </div>
                {active ? (
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 space-y-1 bg-gradient-to-t from-black/70 to-transparent px-2.5 pb-2 pt-6">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/20">
                      <div className="h-full rounded-full bg-signal transition-all" style={{ width: `${Math.max(4, task.progress)}%` }} />
                    </div>
                    <div className="text-right text-[11px] text-white/85">{task.progress}%</div>
                  </div>
                ) : null}
              </div>

              <button
                type="button"
                onClick={() => onSelect(task)}
                className="block w-full space-y-1.5 p-2.5 text-left transition hover:bg-white/[0.04]"
              >
                <div
                  className={cn(
                    'line-clamp-2 min-h-[2.4em] text-xs leading-5',
                    task.status === 'failed' || task.status === 'violation' ? 'text-danger' : 'text-paper',
                  )}
                >
                  {task.status === 'failed' || task.status === 'violation' ? task.error || statusLabel(task.status) : task.prompt}
                </div>
                <div className="flex items-center justify-between gap-2 text-[11px] text-mist">
                  <span className="truncate" title={taskModelLabel(task)}>
                    {taskModelLabel(task)}
                  </span>
                  <span className="shrink-0">{formatTime(task.created_at)}</span>
                </div>
                {!active ? <div className="truncate text-[11px] text-mist/80">{taskParamSummary(task)}</div> : null}
              </button>

              <div className="absolute right-2 top-2 flex gap-1 opacity-100 transition lg:opacity-0 lg:group-hover:opacity-100">
                <ActionButton title="复用参数" onClick={() => onReuse(task)}>
                  <RotateCcw size={14} />
                </ActionButton>
                {canDownload && thumb?.url ? (
                  <a
                    href={thumb.url}
                    download
                    onClick={(event) => event.stopPropagation()}
                    title="下载"
                    aria-label="下载"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-black/55 text-white backdrop-blur transition hover:bg-black/80"
                  >
                    <Download size={14} />
                  </a>
                ) : null}
                <ActionButton title="删除" tone="danger" onClick={() => onDelete(task)}>
                  <Trash2 size={14} />
                </ActionButton>
              </div>
            </div>
          )
        })}
      </div>
      {preview ? <ImageLightbox url={preview.url} alt={preview.alt} onClose={() => setPreview(null)} /> : null}
    </>
  )
}

export default GenerationWorks
