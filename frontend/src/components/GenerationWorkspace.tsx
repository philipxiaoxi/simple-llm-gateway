import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cog, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api, type GenerationTask } from '../lib/api'
import { isTaskActive } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { GenerationComposer } from './GenerationComposer'
import { GenerationConfigDialog } from './GenerationConfigDialog'
import { useGenerationSettings } from './useGenerationSettings'
import { GenerationTaskDialog } from './GenerationTaskDialog'
import { GenerationWorks } from './GenerationWorks'
import { Button, Card } from './ui'

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
  const [status, setStatus] = useState('')
  const [limit, setLimit] = useState(24)
  const [selected, setSelected] = useState<string | null>(null)

  const tasksQuery = useQuery({
    queryKey: ['generation-tasks', kind, status, limit],
    queryFn: () => api.generationTasks({ kind, status, page_size: limit }),
    refetchInterval: (q) =>
      q.state.data?.items.some((task) => isTaskActive(task.status)) ? 2500 : false,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['generation-tasks', kind] })

  const remove = useMutation({
    mutationFn: (id: string) => api.generationDelete(id),
    onSuccess: () => {
      notifyOk('已删除作品')
      void invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const title = kind === 'image' ? '图片生成' : '视频生成'
  const data = tasksQuery.data
  const tasks = data?.items ?? []

  function countFor(filter: string) {
    if (!data) return 0
    if (filter === '') {
      return Object.values(data.counts).reduce((sum, value) => sum + value, 0)
    }
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

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{title}</h1>
        <div className="flex gap-2">
          <Button type="button" variant="line" onClick={() => void tasksQuery.refetch()}>
            <RefreshCw size={15} /> 刷新
          </Button>
          <Button type="button" variant="line" onClick={() => setConfigOpen(true)}>
            <Cog size={15} /> 生成配置
          </Button>
        </div>
      </div>

      <Card>
        {settings.data ? (
          <GenerationComposer
            kind={kind}
            settings={settings.data}
            onCreated={() => void invalidate()}
            onOpenConfig={() => setConfigOpen(true)}
          />
        ) : settings.isError ? (
          <div className="text-sm text-danger">{errorMessage(settings.error, '加载生成配置失败')}</div>
        ) : (
          <div className="text-sm text-mist">加载中…</div>
        )}
      </Card>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-medium text-paper">作品记录</h2>
          <div className="flex flex-wrap gap-1.5">
            {STATUS_FILTERS.map((filter) => (
              <button
                key={filter.value}
                type="button"
                onClick={() => setStatus(filter.value)}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition',
                  status === filter.value
                    ? 'border-signal/50 bg-signal/15 text-signal'
                    : 'border-line text-mist hover:text-paper',
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
          <GenerationWorks tasks={tasks} onSelect={(task) => setSelected(task.id)} onDelete={handleDelete} />
        )}

        {data && tasks.length < data.total ? (
          <div className="flex justify-center">
            <Button type="button" variant="line" disabled={tasksQuery.isFetching} onClick={() => setLimit((value) => value + 24)}>
              {tasksQuery.isFetching ? '加载中…' : '加载更多'}
            </Button>
          </div>
        ) : null}
      </div>

      {configOpen ? <GenerationConfigDialog open onClose={() => setConfigOpen(false)} /> : null}
      <GenerationTaskDialog taskId={selected} onClose={() => setSelected(null)} onChanged={() => void invalidate()} />
    </div>
  )
}

export default GenerationWorkspace
