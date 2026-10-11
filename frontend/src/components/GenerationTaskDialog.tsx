import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RotateCcw, Trash2, Wand2 } from 'lucide-react'
import { api, type GenerationTask } from '../lib/api'
import { formatBytes, formatTime, isTaskActive, statusLabel, statusTone, taskParamSummary } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Badge, Button, Dialog } from './ui'

export function GenerationTaskDialog({
  taskId,
  onClose,
  onChanged,
  onReuse,
}: {
  taskId: string | null
  onClose: () => void
  onChanged: () => void
  onReuse: (task: GenerationTask) => void
}) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['generation-task', taskId],
    queryFn: () => api.generationTask(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (q) => (q.state.data && isTaskActive(q.state.data.status) ? 2500 : false),
  })
  const task = query.data

  const remove = useMutation({
    mutationFn: () => api.generationDelete(taskId as string),
    onSuccess: () => {
      notifyOk('已删除作品')
      onChanged()
      onClose()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const retry = useMutation({
    mutationFn: () => api.generationRetry(taskId as string),
    onSuccess: async () => {
      notifyOk('已重新排队')
      await queryClient.invalidateQueries({ queryKey: ['generation-task', taskId] })
      onChanged()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重试失败')),
  })

  if (!taskId) return null

  const pending = remove.isPending || retry.isPending

  return (
    <Dialog title="作品详情" onClose={pending ? () => undefined : onClose} className="max-w-3xl">
      {!task ? (
        <div className="text-sm text-mist">{query.isError ? errorMessage(query.error, '加载失败') : '加载中…'}</div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(task.status)}>{statusLabel(task.status)}</Badge>
            <span className="text-xs text-mist">{task.model}</span>
            <span className="text-xs text-mist">{formatTime(task.created_at)}</span>
          </div>

          {isTaskActive(task.status) ? (
            <div className="space-y-1">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                <div className="h-full rounded-full bg-signal transition-all" style={{ width: `${Math.max(3, task.progress)}%` }} />
              </div>
              <div className="text-xs text-mist">{statusLabel(task.status)} · {task.progress}%</div>
            </div>
          ) : null}

          {task.error ? (
            <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">{task.error}</div>
          ) : null}

          <div className="space-y-1">
            <div className="text-xs uppercase tracking-[0.16em] text-mist">提示词</div>
            <p className="whitespace-pre-wrap text-sm text-paper">{task.prompt}</p>
          </div>

          {taskParamSummary(task) ? (
            <div className="text-xs text-mist">参数：{taskParamSummary(task)}</div>
          ) : null}

          {task.inputs.length ? (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-[0.16em] text-mist">参考素材</div>
              <div className="flex flex-wrap gap-2">
                {task.inputs.map((asset) =>
                  asset.kind === 'audio' ? (
                    <audio key={asset.id} src={asset.url} controls className="h-8">
                      <track kind="captions" />
                    </audio>
                  ) : (
                    <img key={asset.id} src={asset.url} alt="参考图" className="h-20 w-20 rounded-md border border-line object-cover" />
                  ),
                )}
              </div>
            </div>
          ) : null}

          {task.results.length ? (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-[0.16em] text-mist">生成结果</div>
              <div className="grid gap-3 sm:grid-cols-2">
                {task.results.map((asset) => (
                  <div key={asset.id} className="space-y-1.5">
                    {asset.status === 'ready' && asset.url ? (
                      task.kind === 'video' ? (
                        <video src={asset.url} controls className="w-full rounded-md border border-line">
                          <track kind="captions" />
                        </video>
                      ) : (
                        <img src={asset.url} alt="生成结果" className="w-full rounded-md border border-line" />
                      )
                    ) : (
                      <div className="flex h-32 items-center justify-center rounded-md border border-dashed border-line text-xs text-mist">
                        {asset.status === 'failed' ? asset.error_message || '转存失败' : '结果转存中…'}
                      </div>
                    )}
                    <div className="flex items-center justify-between text-[11px] text-mist">
                      <span>
                        {asset.width && asset.height ? `${asset.width}×${asset.height}` : ''}
                        {asset.size_bytes ? ` ${formatBytes(asset.size_bytes)}` : ''}
                      </span>
                      {asset.url ? (
                        <a href={asset.url} download className="inline-flex items-center gap-1 hover:text-paper">
                          <Download size={12} /> 下载
                        </a>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3">
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="line"
                disabled={pending}
                onClick={() => {
                  onReuse(task)
                  onClose()
                }}
              >
                <Wand2 size={14} /> 复用参数
              </Button>
              {task.status === 'failed' || task.status === 'violation' ? (
                <Button type="button" variant="line" disabled={pending} onClick={() => retry.mutate()}>
                  <RotateCcw size={14} /> 重试
                </Button>
              ) : null}
              <Button type="button" variant="danger" disabled={pending} onClick={() => remove.mutate()}>
                <Trash2 size={14} /> 删除
              </Button>
            </div>
            <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
              关闭
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

export default GenerationTaskDialog
