import { Download, Film, Image as ImageIcon, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import type { GenerationTask } from '../lib/api'
import { formatTime, isTaskActive, resultThumb, statusLabel, statusTone, taskParamSummary } from '../lib/generation'
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

function Cover({ task }: { task: GenerationTask }) {
  const thumb = resultThumb(task)
  // 视频不加载预览（体积大、加载慢），统一用占位图；点开详情再播放
  if (task.kind === 'video') {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 bg-panel-2 text-mist">
        {isTaskActive(task.status) ? <Loader2 size={22} className="animate-spin" /> : <Film size={24} />}
        <span className="text-[11px] text-mist/70">视频</span>
      </div>
    )
  }
  const image = thumb?.thumb_url ?? thumb?.url
  if (image) {
    return <img src={image} alt={task.prompt} className="h-full w-full object-cover" loading="lazy" />
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-panel-2 text-mist">
      {isTaskActive(task.status) ? <Loader2 size={22} className="animate-spin" /> : <ImageIcon size={22} />}
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
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
      {tasks.map((task) => {
        const thumb = resultThumb(task)
        const canDownload = Boolean(thumb?.url)
        return (
          <div
            key={task.id}
            className="group relative overflow-hidden rounded-xl border border-line bg-panel/70 transition hover:border-signal/50 hover:shadow-[0_8px_30px_rgba(0,0,0,0.35)]"
          >
            <button type="button" onClick={() => onSelect(task)} className="block w-full text-left">
              <div className="relative aspect-square overflow-hidden bg-panel-2">
                <Cover task={task} />
                <div className="absolute left-2 top-2">
                  <Badge tone={statusTone(task.status)}>{statusLabel(task.status)}</Badge>
                </div>
                {isTaskActive(task.status) ? (
                  <div className="absolute inset-x-0 bottom-0 space-y-1 bg-gradient-to-t from-black/70 to-transparent px-2.5 pb-2 pt-6">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/20">
                      <div className="h-full rounded-full bg-signal transition-all" style={{ width: `${Math.max(4, task.progress)}%` }} />
                    </div>
                    <div className="text-right text-[11px] text-white/85">{task.progress}%</div>
                  </div>
                ) : null}
              </div>
              <div className="space-y-1.5 p-2.5">
                <div
                  className={cn(
                    'line-clamp-2 min-h-[2.4em] text-xs leading-5',
                    task.status === 'failed' || task.status === 'violation' ? 'text-danger' : 'text-paper',
                  )}
                >
                  {task.status === 'failed' || task.status === 'violation' ? task.error || statusLabel(task.status) : task.prompt}
                </div>
                <div className="flex items-center justify-between gap-2 text-[11px] text-mist">
                  <span className="truncate">{task.model}</span>
                  <span className="shrink-0">{formatTime(task.created_at)}</span>
                </div>
                {!isTaskActive(task.status) ? <div className="truncate text-[11px] text-mist/80">{taskParamSummary(task)}</div> : null}
              </div>
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
  )
}

export default GenerationWorks
