import { Film, Image as ImageIcon, Loader2, Trash2 } from 'lucide-react'
import type { GenerationTask } from '../lib/api'
import { formatTime, isTaskActive, resultThumb, statusLabel, statusTone, taskParamSummary } from '../lib/generation'
import { cn } from '../lib/utils'
import { Badge } from './ui'

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div className="h-full rounded-full bg-signal transition-all" style={{ width: `${Math.max(3, value)}%` }} />
    </div>
  )
}

function Cover({ task }: { task: GenerationTask }) {
  const thumb = resultThumb(task)
  if (thumb?.url && task.kind === 'video') {
    return (
      <video src={thumb.url} className="h-full w-full object-cover" muted playsInline preload="metadata">
        <track kind="captions" />
      </video>
    )
  }
  if (thumb?.url) {
    return <img src={thumb.url} alt={task.prompt} className="h-full w-full object-cover" loading="lazy" />
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-panel-2 text-mist">
      {isTaskActive(task.status) ? (
        <Loader2 size={22} className="animate-spin" />
      ) : task.kind === 'video' ? (
        <Film size={22} />
      ) : (
        <ImageIcon size={22} />
      )}
    </div>
  )
}

export function GenerationWorks({
  tasks,
  onSelect,
  onDelete,
}: {
  tasks: GenerationTask[]
  onSelect: (task: GenerationTask) => void
  onDelete: (task: GenerationTask) => void
}) {
  if (!tasks.length) {
    return <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-mist">还没有作品，提交一个生成任务试试。</div>
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {tasks.map((task) => (
        <button
          key={task.id}
          type="button"
          onClick={() => onSelect(task)}
          className="group overflow-hidden rounded-lg border border-line bg-panel/80 text-left transition hover:border-signal/50"
        >
          <div className="relative aspect-square overflow-hidden">
            <Cover task={task} />
            <div className="absolute left-2 top-2">
              <Badge tone={statusTone(task.status)}>{statusLabel(task.status)}</Badge>
            </div>
            <span
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation()
                onDelete(task)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.stopPropagation()
                  onDelete(task)
                }
              }}
              className="absolute right-2 top-2 inline-flex h-7 w-7 items-center justify-center rounded-md bg-black/50 text-mist opacity-0 transition hover:text-danger group-hover:opacity-100"
              aria-label="删除作品"
            >
              <Trash2 size={14} />
            </span>
          </div>
          <div className="space-y-1.5 p-2.5">
            <div className={cn('truncate text-xs', task.status === 'failed' || task.status === 'violation' ? 'text-danger' : 'text-paper')}>
              {task.status === 'failed' || task.status === 'violation' ? task.error || statusLabel(task.status) : task.prompt}
            </div>
            <div className="flex items-center justify-between gap-2 text-[11px] text-mist">
              <span className="truncate">{task.model}</span>
              <span className="shrink-0">{formatTime(task.created_at)}</span>
            </div>
            {isTaskActive(task.status) ? (
              <ProgressBar value={task.progress} />
            ) : (
              <div className="truncate text-[11px] text-mist/80">{taskParamSummary(task)}</div>
            )}
          </div>
        </button>
      ))}
    </div>
  )
}

export default GenerationWorks
