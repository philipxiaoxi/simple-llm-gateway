import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useId, useState } from 'react'
import { ApiError, api, type LogContentMigration } from '../lib/api'
import { cn, errorMessage, formatBytes, formatDuration, formatTime } from '../lib/utils'
import { Badge, Button, Card } from './ui'
import { Dialog } from './ui/dialog'

type State = LogContentMigration['state']

const STATES = {
  idle: { label: '未开始', tone: 'mist' },
  scanning: { label: '扫描中', tone: 'info' },
  copying: { label: '复制中', tone: 'info' },
  verifying: { label: '校验中', tone: 'info' },
  paused: { label: '已暂停', tone: 'warn' },
  failed: { label: '迁移失败', tone: 'bad' },
  completed: { label: '已完成', tone: 'ok' },
} as const

const CLEANUP_STATES = {
  unavailable: { label: '暂不可用', tone: 'mist' },
  idle: { label: '未开始', tone: 'mist' },
  cleaning: { label: '清理中', tone: 'info' },
  paused: { label: '已暂停', tone: 'warn' },
  failed: { label: '清理失败', tone: 'bad' },
  completed: { label: '已完成', tone: 'ok' },
} as const

const PHASES = [
  { state: 'scanning', label: '扫描', bytes: 'scanned_bytes', rows: 'scanned_rows' },
  { state: 'copying', label: '复制', bytes: 'copied_bytes', rows: 'copied_rows' },
  { state: 'verifying', label: '校验', bytes: 'verified_bytes', rows: 'verified_rows' },
] as const

function isActive(state: State | undefined) {
  return PHASES.some((phase) => phase.state === state)
}

function isAccessError(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403)
}

function storageSize(bytes: number) {
  const gib = 1024 ** 3
  return bytes >= gib ? `${(bytes / gib).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} GiB` : formatBytes(bytes)
}

export function LogContentMigrationCard({ username }: { username: string }) {
  const titleId = useId()
  const queryClient = useQueryClient()
  const queryKey = ['log-content-migration', username] as const
  const mutation = useMutation({
    mutationFn: api.updateLogContentMigration,
    retry: false,
    onMutate: () => queryClient.cancelQueries({ queryKey }),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
    onError: (error) => {
      if (!isAccessError(error)) void queryClient.invalidateQueries({ queryKey, refetchType: 'none' })
    },
  })
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.logContentMigration(signal),
    enabled: !mutation.isPending && !isAccessError(mutation.error),
    retry: false,
    staleTime: 1000,
    refetchInterval: ({ state }) => isActive(state.data?.state) && !isAccessError(state.error) ? 2000 : false,
    refetchOnWindowFocus: ({ state }) => !isAccessError(state.error),
    refetchOnReconnect: ({ state }) => !isAccessError(state.error),
  })

  const denied = isAccessError(query.error) || isAccessError(mutation.error)
  const migration = denied ? undefined : query.data
  const state = migration?.state
  const active = isActive(state)
  const status = state ? STATES[state] : undefined
  const action = active ? 'pause' : state === 'idle' || state === 'paused' || state === 'failed' ? 'start' : null
  const actionLabel = active ? '暂停迁移' : state === 'idle' ? '迁移全部历史正文' : '继续迁移'
  const phaseLabel = migration?.phase
    ? PHASES.find((phase) => phase.state === migration.phase)?.label ?? migration.phase
    : null
  const metrics = migration ? [
    { label: '已耗时', value: migration.elapsed_seconds === 0 ? '0 s' : formatDuration(migration.elapsed_seconds * 1000) },
    { label: '处理速率', value: migration.bytes_per_second == null ? '—' : `${storageSize(migration.bytes_per_second)}/s` },
    { label: '预计剩余', value: migration.remaining_seconds === 0 ? '0 s' : formatDuration(migration.remaining_seconds == null ? null : migration.remaining_seconds * 1000) },
    { label: '预计完成时间（UTC+8）', value: formatTime(migration.estimated_finish_at) },
    ...(migration.free_bytes == null ? [] : [{ label: '可用磁盘空间', value: storageSize(migration.free_bytes) }]),
  ] : []

  return (
    <section aria-labelledby={titleId}>
      <Card className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id={titleId} className="font-semibold text-paper">日志内容迁移</h2>
              <span role="status">
                {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
              </span>
            </div>
            <p className="mt-1 text-sm text-mist">扫描、复制并校验日志内容。迁移步骤会保留原始数据。</p>
          </div>
          {!denied ? (
            <div className="flex flex-wrap gap-2 sm:shrink-0">
              <Button
                type="button"
                variant="line"
                disabled={query.isFetching || mutation.isPending}
                onClick={() => void query.refetch()}
              >
                <RefreshCw size={14} className={cn(query.isFetching && 'animate-spin')} aria-hidden="true" />
                {query.isError ? '重试查询' : '刷新状态'}
              </Button>
              {action ? (
                <Button
                  type="button"
                  variant={action === 'pause' ? 'line' : undefined}
                  disabled={mutation.isPending || query.isError}
                  onClick={() => mutation.mutate(action)}
                >
                  {mutation.isPending ? '正在处理…' : actionLabel}
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>

        {denied ? <p role="alert" className="text-sm text-danger">当前会话无权访问日志内容迁移。</p> : null}
        {!denied && query.isPending ? <p role="status" className="text-sm text-mist">正在读取迁移状态…</p> : null}
        {!denied && query.isError ? (
          <p role="alert" className="break-words text-sm text-danger">
            状态读取失败：{errorMessage(query.error, '请重试。')}{migration ? ' 下方为上次读取的状态。' : ''}
          </p>
        ) : null}
        {!denied && mutation.isError ? (
          <p role="alert" className="break-words text-sm text-danger">
            操作失败：{errorMessage(mutation.error, '请重试。')}
          </p>
        ) : null}
        {migration ? (
          <>
            {migration.error || state === 'failed' ? (
              <p role="alert" className="break-words text-sm text-danger">{migration.error || '迁移失败，可继续迁移。'}</p>
            ) : null}
            {migration.dropped_content ? (
              <p className="text-sm text-warn">本次服务运行期间有 {migration.dropped_content.toLocaleString('zh-CN')} 次新正文采集被跳过或写入失败。</p>
            ) : null}
            {phaseLabel ? <p className="break-words text-sm text-mist">当前阶段：{phaseLabel}</p> : null}
            <div className="grid gap-2 md:grid-cols-3">
              {PHASES.map(({ state: phase, label, bytes, rows }) => {
                const hasTotal = migration.total_bytes > 0 || migration.total_rows > 0
                const useBytes = migration.total_bytes > 0
                const percent = state === 'completed' ? 100 : hasTotal ? Math.max(0, Math.min(100,
                  (useBytes ? migration[bytes] / migration.total_bytes : migration[rows] / migration.total_rows) * 100,
                )) : undefined
                return (
                  <div key={phase} className="min-w-0 space-y-1 rounded-lg border border-line/70 bg-elevate/40 px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-sm font-medium text-paper">
                      <span>{label}</span>
                      {percent != null ? <span className="text-xs tabular-nums text-mist">{Math.floor(percent)}%</span> : null}
                    </div>
                    {percent != null || state === phase ? (
                      <progress aria-label={`${label}进度`} value={percent} max={100} className="h-1.5 w-full accent-signal" />
                    ) : null}
                    <div className="flex flex-wrap gap-x-3 text-xs tabular-nums text-mist">
                      <span>{storageSize(migration[bytes])} / {storageSize(migration.total_bytes)}</span>
                      <span>{migration[rows].toLocaleString('zh-CN')} / {migration.total_rows.toLocaleString('zh-CN')} 条</span>
                    </div>
                  </div>
                )
              })}
            </div>
            <dl className="grid grid-cols-2 gap-3 text-xs lg:grid-cols-5">
              {metrics.map(({ label, value }) => (
                <div key={label} className="min-w-0">
                  <dt className="text-mist">{label}</dt>
                  <dd className="mt-1 break-words tabular-nums text-paper">{value}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : null}
        {state === 'completed' ? <LogContentCleanupSection username={username} /> : null}
      </Card>
    </section>
  )
}

function LogContentCleanupSection({ username }: { username: string }) {
  const titleId = useId()
  const [backupConfirmed, setBackupConfirmed] = useState<boolean | null>(null)
  const queryClient = useQueryClient()
  const queryKey = ['log-content-cleanup', username] as const
  const mutation = useMutation({
    mutationFn: (action: 'start' | 'pause') => action === 'start'
      ? api.startLogContentCleanup(true)
      : api.pauseLogContentCleanup(),
    retry: false,
    onMutate: () => queryClient.cancelQueries({ queryKey }),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
    onError: (error) => {
      if (!isAccessError(error)) void queryClient.invalidateQueries({ queryKey, refetchType: 'none' })
    },
  })
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.logContentCleanup(signal),
    enabled: !mutation.isPending && !isAccessError(mutation.error),
    retry: false,
    staleTime: 1000,
    refetchInterval: ({ state }) => state.data?.state === 'cleaning' && !isAccessError(state.error) ? 2000 : false,
    refetchOnWindowFocus: ({ state }) => !isAccessError(state.error),
    refetchOnReconnect: ({ state }) => !isAccessError(state.error),
  })

  const denied = isAccessError(query.error) || isAccessError(mutation.error)
  const cleanup = denied ? undefined : query.data
  const state = cleanup?.state
  const status = state ? CLEANUP_STATES[state] : undefined
  const startLabel = state === 'idle' ? '开始清理' : state === 'paused' ? '继续清理' : state === 'failed' ? '重试清理' : null
  const busy = mutation.isPending || query.isError
  const percent = !cleanup ? undefined : state === 'completed' ? 100 : cleanup.total_rows > 0
    ? Math.max(0, Math.min(100, cleanup.cleaned_rows / cleanup.total_rows * 100))
    : undefined
  const metrics = cleanup ? [
    { label: '已耗时', value: cleanup.elapsed_seconds === 0 ? '0 s' : formatDuration(cleanup.elapsed_seconds * 1000) },
    { label: '预计剩余', value: cleanup.remaining_seconds === 0 ? '0 s' : formatDuration(cleanup.remaining_seconds == null ? null : cleanup.remaining_seconds * 1000) },
    { label: '预计完成时间（UTC+8）', value: formatTime(cleanup.estimated_finish_at) },
    { label: 'SQLite 可复用空间', value: cleanup.reusable_bytes == null ? '—' : storageSize(cleanup.reusable_bytes) },
  ] : []

  return (
    <section aria-labelledby={titleId} className="space-y-3 border-t border-line pt-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={titleId} className="font-semibold text-paper">清理原库日志正文</h3>
            <span role="status">
              {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
            </span>
          </div>
          {!denied ? (
            <p className="mt-1 text-sm text-mist">
              清理原库中的旧正文副本，保留已迁移的正文和日志记录。清理产生的空间可由 SQLite 在原库内复用，数据库文件通常不会缩小，系统不会自动执行 VACUUM。
            </p>
          ) : null}
        </div>
        {!denied ? (
          <div className="flex flex-wrap gap-2 sm:shrink-0">
            <Button type="button" variant="line" disabled={query.isFetching || mutation.isPending} onClick={() => void query.refetch()}>
              <RefreshCw size={14} className={cn(query.isFetching && 'animate-spin')} aria-hidden="true" />
              {query.isError ? '重试查询' : '刷新清理状态'}
            </Button>
            {startLabel ? (
              <Button type="button" variant="danger" disabled={busy} onClick={() => setBackupConfirmed(false)}>{startLabel}</Button>
            ) : state === 'cleaning' ? (
              <Button type="button" variant="line" disabled={busy} onClick={() => mutation.mutate('pause')}>暂停清理</Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {denied ? <p role="alert" className="text-sm text-danger">当前会话无权访问日志正文清理。</p> : null}
      {!denied && query.isPending ? <p role="status" className="text-sm text-mist">正在读取清理状态…</p> : null}
      {!denied && mutation.isPending ? <p role="status" className="text-sm text-mist">正在处理清理请求…</p> : null}
      {!denied && query.isError ? (
        <p role="alert" className="break-words text-sm text-danger">
          清理状态读取失败：{errorMessage(query.error, '请重试。')}{cleanup ? ' 下方为上次读取的状态。' : ''}
        </p>
      ) : null}
      {!denied && mutation.isError ? (
        <p role="alert" className="break-words text-sm text-danger">清理操作失败：{errorMessage(mutation.error, '请重试。')}</p>
      ) : null}
      {cleanup?.error || state === 'failed' ? (
        <p role="alert" className="break-words text-sm text-danger">{cleanup?.error || '清理失败，可重试。'}</p>
      ) : null}
      {cleanup && state !== 'unavailable' ? (
        <>
          <div className="space-y-1 rounded-lg border border-line/70 bg-elevate/40 px-3 py-2">
            <div className="flex items-center justify-between gap-2 text-sm font-medium text-paper">
              <span>清理进度</span>
              {percent != null ? <span className="text-xs tabular-nums text-mist">{Math.floor(percent)}%</span> : null}
            </div>
            {percent != null || state === 'cleaning' ? (
              <progress aria-label="日志正文清理进度" value={percent} max={100} className="h-1.5 w-full accent-signal" />
            ) : null}
            <div className="flex flex-wrap gap-x-3 text-xs tabular-nums text-mist">
              <span>{cleanup.cleaned_rows.toLocaleString('zh-CN')} / {cleanup.total_rows.toLocaleString('zh-CN')} 条</span>
              <span>{storageSize(cleanup.cleaned_bytes)} / {storageSize(cleanup.total_bytes)}</span>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-3 text-xs lg:grid-cols-4">
            {metrics.map(({ label, value }) => (
              <div key={label} className="min-w-0">
                <dt className="text-mist">{label}</dt>
                <dd className="mt-1 break-words tabular-nums text-paper">{value}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : null}
      {backupConfirmed !== null && !denied ? (
        <Dialog
          title="确认清理原库日志正文"
          onClose={() => setBackupConfirmed(null)}
          footer={(
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="line" onClick={() => setBackupConfirmed(null)}>取消</Button>
              <Button
                type="button"
                variant="danger"
                disabled={!backupConfirmed || !startLabel || busy}
                onClick={() => {
                  if (!backupConfirmed || !startLabel || busy) return
                  setBackupConfirmed(null)
                  mutation.mutate('start')
                }}
              >
                确认{startLabel || '清理'}
              </Button>
            </div>
          )}
        >
          <p className="text-sm text-mist">此操作会永久移除原库中的旧正文副本。请先完成数据库备份，并确认备份可用于恢复。</p>
          <label className="mt-4 flex items-start gap-2 text-sm text-paper">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-signal"
              checked={backupConfirmed}
              onChange={(event) => setBackupConfirmed(event.currentTarget.checked)}
            />
            我已完成数据库备份，并确认备份可用于恢复。
          </label>
        </Dialog>
      ) : null}
    </section>
  )
}
