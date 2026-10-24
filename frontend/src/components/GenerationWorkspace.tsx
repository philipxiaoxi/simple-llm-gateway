import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cog, Plus, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type GenerationTask } from '../lib/api'
import { isTaskActive, presetFromTask, type GenerationPreset } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { GenerationComposer } from './GenerationComposer'
import { GenerationConfigDialog } from './GenerationConfigDialog'
import { Pagination } from './Pagination'
import { useGenerationSettings } from './useGenerationSettings'
import { GenerationTaskDialog } from './GenerationTaskDialog'
import { GenerationWorks } from './GenerationWorks'
import { useCan } from '../lib/session'
import { Button, Dialog } from './ui'

const PAGE_SIZE = 24
const STATUS_FILTERS = [
  { value: '', label: '全部' },
  { value: 'running', label: '生成中' },
  { value: 'succeeded', label: '已完成' },
  { value: 'failed', label: '失败' },
]

export function GenerationWorkspace({ kind }: { kind: 'image' | 'video' }) {
  const queryClient = useQueryClient()
  const settings = useGenerationSettings()
  const [configOpen, setConfigOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [preset, setPreset] = useState<GenerationPreset | null>(null)

  const tasksQuery = useQuery({
    queryKey: ['generation-tasks', kind, status, page],
    queryFn: () => api.generationTasks({ kind, status, page, page_size: PAGE_SIZE }),
    refetchInterval: (q) => (q.state.data?.items.some((task) => isTaskActive(task.status)) ? 2500 : false),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['generation-tasks', kind] })

  useEffect(() => {
    setPage(1)
  }, [status])

  const remove = useMutation({
    mutationFn: (id: string) => api.generationDelete(id),
    onSuccess: () => {
      notifyOk('已删除作品')
      void invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const canCreate = useCan(`${kind}_gen:task_create`)
  const canConfig = useCan('generation:settings')
  const title = kind === 'image' ? '图片生成' : '视频生成'
  const data = tasksQuery.data
  const tasks = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const runningCount = data
    ? (data.counts.queued ?? 0) + (data.counts.submitting ?? 0) + (data.counts.running ?? 0)
    : 0

  useEffect(() => {
    if (data && page > Math.max(1, Math.ceil(data.total / PAGE_SIZE))) {
      setPage(Math.max(1, Math.ceil(data.total / PAGE_SIZE)))
    }
  }, [data, page])

  function countFor(filter: string) {
    if (!data) return 0
    if (filter === '') return Object.values(data.counts).reduce((sum, value) => sum + value, 0)
    if (filter === 'running') {
      return (data.counts.queued ?? 0) + (data.counts.submitting ?? 0) + (data.counts.running ?? 0)
    }
    if (filter === 'failed') {
      return (data.counts.failed ?? 0) + (data.counts.violation ?? 0)
    }
    return data.counts[filter] ?? 0
  }

  function handleDelete(task: GenerationTask) {
    if (window.confirm('删除该作品？生成结果文件也会一并删除。')) remove.mutate(task.id)
  }

  function openCreate(next?: GenerationPreset | null) {
    setPreset(next ?? null)
    setCreateOpen(true)
  }

  function handleReuse(task: GenerationTask) {
    openCreate(presetFromTask(task))
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{title}</h1>
        <div className="flex gap-2">
          {canCreate ? (
          <Button type="button" onClick={() => openCreate()}>
            <Plus size={15} /> 生成{kind === 'image' ? '图片' : '视频'}
          </Button>
          ) : null}
          <Button type="button" variant="line" onClick={() => void tasksQuery.refetch()}>
            <RefreshCw size={15} /> 刷新
          </Button>
          {canConfig ? (
          <Button type="button" variant="line" onClick={() => setConfigOpen(true)}>
            <Cog size={15} /> 生成配置
          </Button>
          ) : null}
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-medium text-paper">
            作品记录
            {runningCount > 0 ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-signal/15 px-2 py-0.5 text-[11px] text-signal">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-signal" />
                生成中 {runningCount}
              </span>
            ) : null}
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {STATUS_FILTERS.map((filter) => (
              <button
                key={filter.value}
                type="button"
                onClick={() => setStatus(filter.value)}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition',
                  status === filter.value ? 'border-signal/50 bg-signal/15 text-signal' : 'border-line text-mist hover:text-paper',
                )}
              >
                {filter.label}
                {data ? ` ${countFor(filter.value)}` : ''}
              </button>
            ))}
          </div>
        </div>

        {tasksQuery.isError ? (
          <div className="text-sm text-danger">{errorMessage(tasksQuery.error, '加载作品失败')}</div>
        ) : (
          <GenerationWorks
            kind={kind}
            tasks={tasks}
            onSelect={(task) => setSelected(task.id)}
            onDelete={handleDelete}
            onReuse={handleReuse}
            onCreate={() => openCreate()}
          />
        )}

        {total > PAGE_SIZE ? <Pagination page={page} pageCount={pageCount} total={total} unit="个作品" onPage={setPage} /> : null}
      </div>

      {createOpen ? (
        <Dialog title={`生成${kind === 'image' ? '图片' : '视频'}`} onClose={() => setCreateOpen(false)} className="max-w-2xl">
          {settings.data ? (
            <GenerationComposer
              kind={kind}
              settings={settings.data}
              preset={preset}
              onCreated={() => {
                setCreateOpen(false)
                setPage(1)
                void invalidate()
              }}
              onOpenConfig={() => {
                setCreateOpen(false)
                setConfigOpen(true)
              }}
            />
          ) : settings.isError ? (
            <div className="text-sm text-danger">{errorMessage(settings.error, '加载生成配置失败')}</div>
          ) : (
            <div className="text-sm text-mist">加载中…</div>
          )}
        </Dialog>
      ) : null}

      {configOpen ? <GenerationConfigDialog open onClose={() => setConfigOpen(false)} /> : null}
      <GenerationTaskDialog
        taskId={selected}
        onClose={() => setSelected(null)}
        onChanged={() => void invalidate()}
        onReuse={handleReuse}
      />
    </div>
  )
}

export default GenerationWorkspace
