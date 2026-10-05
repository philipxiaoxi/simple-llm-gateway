import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CircleAlert, Pencil, Plus, RefreshCw, Send, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { TikHubConfigDialog, TIKHUB_QUERY_KEY } from '../components/TikHubConfigDialog'
import { Badge, Button, Card, Dialog, Field, Input, Select, Switch } from '../components/ui'
import { api, type InfoSource, type InfoSourcePreview } from '../lib/api'
import { formatCount, formatInterval, relativeTime } from '../lib/info'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

const INTERVAL_OPTIONS = [
  { value: 300, label: '5 分钟' },
  { value: 900, label: '15 分钟' },
  { value: 1800, label: '30 分钟' },
  { value: 3600, label: '1 小时' },
  { value: 7200, label: '2 小时' },
  { value: 21600, label: '6 小时' },
]

async function invalidateSources(client: ReturnType<typeof useQueryClient>) {
  await client.invalidateQueries({ queryKey: ['info-sources'] })
  await client.invalidateQueries({ queryKey: ['info-stats'] })
  await client.invalidateQueries({ queryKey: ['info-items'] })
}

export function InfoSourcesPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['info-sources'], queryFn: api.infoSources })
  const sources = query.data?.sources ?? []
  const provider = useQuery({ queryKey: TIKHUB_QUERY_KEY, queryFn: api.tikhubStatus })

  const [addOpen, setAddOpen] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [editing, setEditing] = useState<InfoSource | null>(null)
  const [removing, setRemoving] = useState<InfoSource | null>(null)
  const [collectingId, setCollectingId] = useState('')

  const collect = useMutation({
    mutationFn: (id: string) => api.infoSourceCollect(id),
    onSuccess: async (result) => {
      if (result.error) notifyBad(`采集失败：${result.error}`)
      else notifyOk(`采集完成：新增 ${result.created} 条，拉取 ${result.fetched} 条，跳过 ${result.skipped} 条`)
      await invalidateSources(queryClient)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '采集失败')),
  })

  const toggle = useMutation({
    mutationFn: (payload: { id: string; enabled: boolean }) =>
      api.infoSourceUpdate(payload.id, { enabled: payload.enabled }),
    onSuccess: async () => {
      await invalidateSources(queryClient)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  function collectNow(source: InfoSource) {
    setCollectingId(source.id)
    collect.mutate(source.id, { onSettled: () => setCollectingId('') })
  }

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1080px] space-y-4">
      <Link
        to="/info"
        className="inline-flex items-center gap-1 text-sm text-mist transition hover:text-paper"
      >
        <ArrowLeft size={15} /> 返回瀑布流
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold">渠道管理</h2>
          <p className="mt-1 text-xs text-mist">
            采集公开 Telegram 频道的内容，媒体会转存到平台本地，前端不直连上游。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="line" onClick={() => setConfigOpen(true)}>
            TikHub 凭据
          </Button>
          <Button type="button" variant="line" onClick={() => setAddOpen(true)}>
            <Plus size={15} /> 添加渠道
          </Button>
        </div>
      </div>

      {provider.data && !provider.data.configured ? (
        <div className="flex flex-col gap-2 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2.5 text-xs text-warn sm:flex-row sm:items-center sm:justify-between">
          <span className="flex items-start gap-2">
            <CircleAlert size={14} className="mt-0.5 shrink-0" />
            TikHub 凭据未配置：资讯采集与抖音下载共用同一份 TikHub API Key，配置后才能拉取频道内容。
          </span>
          <button
            type="button"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-warn/40 px-2 py-1 text-warn transition hover:bg-warn/15"
            onClick={() => setConfigOpen(true)}
          >
            配置 TikHub 凭据
          </button>
        </div>
      ) : null}

      {query.isError ? (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {errorMessage(query.error, '加载渠道失败')}
        </div>
      ) : null}

      {query.isLoading ? <div className="text-sm text-mist">加载中…</div> : null}

      {!query.isLoading && !query.isError && sources.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-14 text-center">
          <p className="text-sm text-mist">还没有采集渠道，先添加一个 Telegram 频道</p>
          <Button type="button" className="mt-4" onClick={() => setAddOpen(true)}>
            <Plus size={15} /> 添加 Telegram 频道
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
        {sources.map((source) => (
          <Card key={source.id} className="p-3.5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <SourceAvatar name={source.title} url={source.avatar_url} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 truncate text-sm font-medium text-paper">
                      {source.title || source.identifier}
                    </span>
                    <Badge tone="info">Telegram</Badge>
                    {source.subscriber_count_text ? (
                      <span className="text-[11px] text-mist">{source.subscriber_count_text} 订阅</span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-mist">
                    @{source.username || source.identifier}
                    {source.description ? ` · ${source.description}` : ''}
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-mist">
                    <span>每 {formatInterval(source.poll_interval_seconds)}</span>
                    <span>{formatCount(source.item_count)} 条</span>
                    <span title={formatTime(source.last_success_at)}>
                      {source.last_success_at
                        ? `最近成功 ${relativeTime(source.last_success_at)}`
                        : source.last_polled_at
                          ? `最近采集 ${relativeTime(source.last_polled_at)}`
                          : '尚未采集'}
                    </span>
                    {source.cursor_after != null ? <span>游标 {source.cursor_after}</span> : null}
                  </div>
                  {source.last_error ? (
                    <div className="mt-1.5 flex items-start gap-1.5 text-[11px] text-danger">
                      <CircleAlert size={13} className="mt-0.5 shrink-0" />
                      <span className="min-w-0 break-words">
                        连续失败 {source.consecutive_failures} 次：{source.last_error}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <Switch
                  checked={source.enabled}
                  disabled={toggle.isPending}
                  onCheckedChange={(next) => toggle.mutate({ id: source.id, enabled: next })}
                  onLabel="已启用"
                  offLabel="已停用"
                />
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="line"
                className="min-h-9 px-2.5 py-1.5 text-xs md:min-h-8"
                disabled={collect.isPending && collectingId === source.id}
                onClick={() => collectNow(source)}
              >
                <RefreshCw
                  size={14}
                  className={collect.isPending && collectingId === source.id ? 'animate-spin' : undefined}
                />
                {collect.isPending && collectingId === source.id ? '采集中…' : '立即采集'}
              </Button>
              <Button
                type="button"
                variant="line"
                className="min-h-9 px-2.5 py-1.5 text-xs md:min-h-8"
                onClick={() => setEditing(source)}
              >
                <Pencil size={14} /> 编辑
              </Button>
              <Button
                type="button"
                variant="danger"
                className="min-h-9 px-2.5 py-1.5 text-xs md:min-h-8"
                onClick={() => setRemoving(source)}
              >
                <Trash2 size={14} /> 删除
              </Button>
            </div>
          </Card>
        ))}
      </div>

      {addOpen ? <AddSourceDialog onClose={() => setAddOpen(false)} /> : null}
      {editing ? <EditSourceDialog source={editing} onClose={() => setEditing(null)} /> : null}
      {removing ? <DeleteSourceDialog source={removing} onClose={() => setRemoving(null)} /> : null}
      {configOpen ? <TikHubConfigDialog open onClose={() => setConfigOpen(false)} /> : null}
    </div>
  )
}

function SourceAvatar({ name, url }: { name: string; url: string }) {
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
  }, [url])

  const initial = (name || '?').trim().slice(0, 1).toUpperCase() || '?'
  if (!url || failed) {
    return (
      <span
        aria-hidden="true"
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line bg-panel-2 text-sm text-mist"
      >
        {initial}
      </span>
    )
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-10 w-10 shrink-0 rounded-full border border-line bg-panel-2 object-cover"
    />
  )
}

function AddSourceDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [raw, setRaw] = useState('')
  const [title, setTitle] = useState('')
  const [interval, setInterval] = useState(1800)
  const [preview, setPreview] = useState<InfoSourcePreview | null>(null)

  const previewMutation = useMutation({
    mutationFn: () => api.infoSourcePreview(raw.trim()),
    onSuccess: (data) => {
      setPreview(data)
      setTitle(data.title || '')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '无法识别该频道')),
  })

  const createMutation = useMutation({
    mutationFn: async () => {
      const created = await api.infoSourceCreate({
        raw: raw.trim(),
        title: title.trim() || undefined,
        poll_interval_seconds: interval,
        enabled: true,
      })
      const result = await api.infoSourceCollect(created.id).catch(() => null)
      return { created, result }
    },
    onSuccess: async ({ created, result }) => {
      if (result?.error) notifyBad(`已添加「${created.title || created.identifier}」，但首次采集失败：${result.error}`)
      else
        notifyOk(
          `已添加「${created.title || created.identifier}」${result ? `，首次采集新增 ${result.created} 条` : ''}`,
        )
      await invalidateSources(queryClient)
      onClose()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '添加失败')),
  })

  const pending = previewMutation.isPending || createMutation.isPending

  return (
    <Dialog title="添加 Telegram 频道" onClose={() => (pending ? undefined : onClose())}>
      <div className="space-y-3">
        <Field label="频道标识">
          <Input
            value={raw}
            disabled={pending}
            onChange={(event) => {
              setRaw(event.target.value)
              setPreview(null)
            }}
            placeholder="https://t.me/telegram 或 @telegram"
          />
        </Field>

        {!preview ? (
          <p className="text-xs text-mist">
            只能采集公开频道（需要有 username）。可以粘贴 `https://t.me/xxx`、`t.me/xxx` 或 `@xxx`。
          </p>
        ) : (
          <div className="rounded-lg border border-line bg-panel-2 p-3">
            <div className="flex items-start gap-3">
              <SourceAvatar name={preview.title} url={preview.avatar_url} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 truncate text-sm font-medium text-paper">
                    {preview.title || preview.identifier}
                  </span>
                  {preview.subscriber_count_text ? (
                    <span className="text-[11px] text-mist">{preview.subscriber_count_text} 订阅</span>
                  ) : null}
                </div>
                <div className="truncate text-[11px] text-mist">@{preview.username || preview.identifier}</div>
                {preview.description ? (
                  <p className="mt-1 line-clamp-3 text-[11px] text-mist">{preview.description}</p>
                ) : null}
              </div>
            </div>
            {preview.already_added ? (
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-warn">
                <CircleAlert size={13} /> 该频道已在列表中，无需重复添加
              </div>
            ) : null}
          </div>
        )}

        {preview && !preview.already_added ? (
          <>
            <Field label="显示标题（留空用上游标题）">
              <Input value={title} disabled={pending} onChange={(event) => setTitle(event.target.value)} />
            </Field>
            <Field label="采集间隔">
              <Select
                value={String(interval)}
                disabled={pending}
                onChange={(event) => setInterval(Number(event.target.value))}
              >
                {INTERVAL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    每 {option.label}
                  </option>
                ))}
              </Select>
            </Field>
          </>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
            取消
          </Button>
          {!preview ? (
            <Button
              type="button"
              disabled={!raw.trim() || previewMutation.isPending}
              onClick={() => previewMutation.mutate()}
            >
              {previewMutation.isPending ? '解析中…' : '预览频道'}
            </Button>
          ) : (
            <Button
              type="button"
              disabled={pending || preview.already_added}
              onClick={() => createMutation.mutate()}
            >
              <Send size={14} />
              {createMutation.isPending ? '保存并采集…' : '确认保存并采集'}
            </Button>
          )}
        </div>
      </div>
    </Dialog>
  )
}

function EditSourceDialog({ source, onClose }: { source: InfoSource; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(source.title || '')
  const [interval, setIntervalValue] = useState(source.poll_interval_seconds || 1800)
  const [enabled, setEnabled] = useState(source.enabled)

  const save = useMutation({
    mutationFn: () =>
      api.infoSourceUpdate(source.id, {
        title: title.trim(),
        poll_interval_seconds: interval,
        enabled,
      }),
    onSuccess: async () => {
      notifyOk('渠道已更新')
      await invalidateSources(queryClient)
      onClose()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  return (
    <Dialog title={`编辑渠道 · ${source.identifier}`} onClose={() => (save.isPending ? undefined : onClose())}>
      <div className="space-y-3">
        <Field label="显示标题">
          <Input value={title} disabled={save.isPending} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label="采集间隔">
          <Select
            value={String(interval)}
            disabled={save.isPending}
            onChange={(event) => setIntervalValue(Number(event.target.value))}
          >
            {INTERVAL_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                每 {option.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
          <div className="text-sm text-paper">参与定时采集</div>
          <Switch checked={enabled} onCheckedChange={setEnabled} disabled={save.isPending} />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={save.isPending} onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function DeleteSourceDialog({ source, onClose }: { source: InfoSource; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [purge, setPurge] = useState(false)

  const remove = useMutation({
    mutationFn: () => api.infoSourceDelete(source.id, purge),
    onSuccess: async () => {
      notifyOk('渠道已删除')
      await invalidateSources(queryClient)
      onClose()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  return (
    <Dialog title="删除渠道" onClose={() => (remove.isPending ? undefined : onClose())}>
      <div className="space-y-3">
        <p className="text-sm text-mist">
          确定删除「{source.title || source.identifier}」？默认只删渠道，已采集的内容会保留。
        </p>
        <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
          <div>
            <div className="text-sm text-paper">连同内容与媒体一起删除</div>
            <div className="text-xs text-mist">会删除该渠道下 {formatCount(source.item_count)} 条内容及其本地媒体文件。</div>
          </div>
          <Switch checked={purge} onCheckedChange={setPurge} disabled={remove.isPending} onLabel="彻底删除" offLabel="仅删渠道" />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={remove.isPending} onClick={onClose}>
            取消
          </Button>
          <Button type="button" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? '删除中…' : '确认删除'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export default InfoSourcesPage
