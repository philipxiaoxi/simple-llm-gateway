import { useEffect, useMemo, useState } from 'react'
import type { ComponentType } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AppWindow, Archive, Code2, Compass, Container, Download, Globe, RefreshCw, RotateCcw, Trash2, TriangleAlert } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../ui'
import { notifyBad, notifyOk } from '../../lib/toast'
import { errorMessage, cn } from '../../lib/utils'
import { relativeTime } from '../../lib/info'
import { createOfflineApi, formatBytes, type OfflineApi, type OfflineCacheItem, type OfflineProvider } from '../../lib/offline'
import { ChromePanel, DockerPanel, EdgePanel, MsStorePanel, VSCodePanel } from './panels'
import { EmptyNote } from './shared'

const PANELS: Record<string, ComponentType<{ api: OfflineApi; onQueued: () => void }>> = {
  vscode: VSCodePanel,
  chrome: ChromePanel,
  edge: EdgePanel,
  docker: DockerPanel,
  msstore: MsStorePanel,
}

const META: Record<string, { label: string; icon: LucideIcon }> = {
  vscode: { label: 'VSCode 插件', icon: Code2 },
  chrome: { label: 'Chrome 扩展', icon: Globe },
  edge: { label: 'Edge 扩展', icon: Compass },
  docker: { label: 'Docker 镜像', icon: Container },
  msstore: { label: 'Microsoft 商店', icon: AppWindow },
}

const FALLBACK: OfflineProvider[] = [
  { slug: 'vscode', label: 'VSCode 插件', short_label: 'VSCode', description: '', placeholder: '' },
  { slug: 'chrome', label: 'Chrome 扩展', short_label: 'Chrome', description: '', placeholder: '' },
  { slug: 'edge', label: 'Edge 扩展', short_label: 'Edge', description: '', placeholder: '' },
  { slug: 'docker', label: 'Docker 镜像', short_label: 'Docker', description: '', placeholder: '' },
  { slug: 'msstore', label: 'Microsoft 商店', short_label: 'MS商店', description: '', placeholder: '' },
]

function CacheCard({
  row,
  api,
  canManage,
  onChanged,
}: {
  row: OfflineCacheItem
  api: OfflineApi
  canManage: boolean
  onChanged: () => void
}) {
  const meta = META[row.provider]
  const FallbackIcon = meta?.icon ?? Globe
  const [imgFailed, setImgFailed] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const format = (row.filename.split('.').pop() || '').toUpperCase()
  // 针对性展示：扩展显示格式+描述；vscode 显示版本；docker 显示架构；其余显示文件类型
  const badge = row.provider === 'vscode' || row.provider === 'docker' ? row.subtitle : format
  const showFilename = Boolean(row.filename) && row.filename !== row.title
  const showDescription = Boolean(row.description)
  const showIcon = row.has_icon && !imgFailed
  const active = row.status === 'queued' || row.status === 'caching'
  const failed = row.status === 'failed'

  function update() {
    if (refreshing) return
    setRefreshing(true)
    api
      .refreshCache(row.id)
      .then(() => {
        notifyOk(failed ? '已重新加入缓存队列' : '已开始更新缓存')
        onChanged()
      })
      .catch((caught) => notifyBad(errorMessage(caught, failed ? '重试失败' : '更新失败')))
      .finally(() => setRefreshing(false))
  }

  const sizeLine = active
    ? `${formatBytes(row.bytes_downloaded)}${row.expected_bytes ? ` / ${formatBytes(row.expected_bytes)}` : ''}`
    : formatBytes(row.size_bytes)

  return (
    <div className="flex min-w-0 flex-col gap-2.5 rounded-xl border border-line bg-panel-2/60 p-3">
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-line bg-ink/50 text-signal">
          {showIcon ? (
            <img
              src={api.cacheIconUrl(row.id)}
              alt=""
              loading="lazy"
              onError={() => setImgFailed(true)}
              className="h-full w-full object-cover"
            />
          ) : (
            <FallbackIcon size={18} />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 truncate text-sm font-medium text-paper" title={row.title}>
              {row.title}
            </span>
            {badge ? (
              <span className="shrink-0 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-mist">{badge}</span>
            ) : null}
          </div>
          {showFilename ? (
            <div className="truncate font-mono text-[11px] text-mist" title={row.filename}>
              {row.filename}
            </div>
          ) : null}
        </div>
      </div>

      {showDescription ? <p className="line-clamp-2 break-words text-xs leading-5 text-mist">{row.description}</p> : null}

      {active ? (
        <div className="space-y-1.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
            <div
              className="h-full rounded-full bg-signal transition-all"
              style={{ width: `${Math.max(4, row.percent)}%` }}
            />
          </div>
          <div className="flex min-w-0 items-center justify-between gap-2 text-[11px] text-mist">
            <span className="min-w-0 truncate">{row.message || (row.status === 'queued' ? '排队中' : '缓存中')} · {row.percent}%</span>
            <span className="shrink-0 font-mono tabular-nums">{sizeLine}</span>
          </div>
        </div>
      ) : failed ? (
        <div className="flex items-start gap-1.5 rounded-lg border border-danger/30 bg-danger/10 px-2.5 py-2 text-[11px] text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          <span className="line-clamp-2 min-w-0 break-words">{row.error_message || '缓存失败'}</span>
        </div>
      ) : null}

      <div className="flex min-w-0 items-center justify-between gap-2 text-[11px] text-mist">
        <span className="min-w-0 truncate">{meta?.label ?? row.provider}</span>
        <span className="shrink-0 whitespace-nowrap">{failed || active ? sizeLine : `${sizeLine} · ${relativeTime(row.created_at)}`}</span>
      </div>

      <div className="mt-auto flex gap-2">
        {row.downloadable ? (
          <Button
            type="button"
            className="flex-1"
            onClick={() => void api.download(api.cacheDownloadUrl(row.id), row.filename)}
          >
            <Download size={14} /> 下载
          </Button>
        ) : (
          <Button type="button" className="flex-1" disabled>
            {active ? (
              <>
                <RefreshCw size={14} className="animate-spin" /> 缓存中 {row.percent}%
              </>
            ) : (
              <>
                <TriangleAlert size={14} /> 缓存失败
              </>
            )}
          </Button>
        )}
        {canManage && failed ? (
          <Button type="button" variant="line" aria-label="重新缓存" disabled={refreshing} onClick={update}>
            <RotateCcw size={14} className={refreshing ? 'animate-spin' : undefined} />
          </Button>
        ) : null}
        {canManage && row.downloadable ? (
          <Button
            type="button"
            variant="line"
            aria-label="更新缓存"
            disabled={refreshing}
            onClick={update}
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />
          </Button>
        ) : null}
        {canManage ? (
          <Button
            type="button"
            variant="line"
            aria-label="删除缓存"
            onClick={() =>
              void api
                .deleteCache(row.id)
                .then(() => {
                  notifyOk('已删除缓存')
                  onChanged()
                })
                .catch((caught) => notifyBad(errorMessage(caught, '删除失败')))
            }
          >
            <Trash2 size={14} />
          </Button>
        ) : null}
      </div>
    </div>
  )
}

/** 离线下载工具主体：来源标签页 + 对应面板 + 已下载缓存。base 决定管理端或公开端。 */
export function OfflineTool({ base }: { base: string }) {
  const queryClient = useQueryClient()
  const api = useMemo(() => createOfflineApi(base), [base])
  const providers = useQuery({ queryKey: ['offline-providers', base], queryFn: api.providers })
  const cacheQuery = useQuery({ queryKey: ['offline-cache', base], queryFn: () => api.cache() })
  const list = providers.data?.providers?.length ? providers.data.providers : FALLBACK
  const [active, setActive] = useState('')

  useEffect(() => {
    if (!active && list.length) setActive(list[0].slug)
  }, [active, list])

  function refreshCache() {
    // 公开端是锚点下载、无法 await，首次落缓存可能稍晚；延迟再刷一次兜底
    void queryClient.invalidateQueries({ queryKey: ['offline-cache', base] })
    window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['offline-cache', base] }), 2500)
  }

  const current = list.find((item) => item.slug === active) ?? list[0]
  const Panel = current ? PANELS[current.slug] : undefined
  const items = useMemo(() => cacheQuery.data?.items ?? [], [cacheQuery.data])
  const canManage = base.startsWith('/api/admin')
  const hasActive = useMemo(
    () => items.some((row) => row.status === 'queued' || row.status === 'caching'),
    [items],
  )

  // 有任务在后台缓存时，轮询缓存列表实时刷新进度
  useEffect(() => {
    if (!hasActive) return
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ['offline-cache', base] })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [hasActive, queryClient, base])

  const [providerFilter, setProviderFilter] = useState('all')
  const [keyword, setKeyword] = useState('')
  const [sort, setSort] = useState<'recent' | 'name' | 'size'>('recent')

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    const rows = items.filter((row) => {
      if (providerFilter !== 'all' && row.provider !== providerFilter) return false
      if (!kw) return true
      return `${row.title} ${row.filename} ${row.subtitle} ${row.description}`.toLowerCase().includes(kw)
    })
    const sorted = [...rows]
    if (sort === 'name') sorted.sort((a, b) => a.title.localeCompare(b.title))
    else if (sort === 'size') sorted.sort((a, b) => b.size_bytes - a.size_bytes)
    else sorted.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))
    return sorted
  }, [items, providerFilter, keyword, sort])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-2">
        {list.map((provider) => {
          const Icon = META[provider.slug]?.icon ?? Globe
          const selected = provider.slug === active
          return (
            <button
              key={provider.slug}
              type="button"
              onClick={() => setActive(provider.slug)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-2 text-xs transition',
                selected
                  ? 'border-signal/40 bg-signal/15 text-signal'
                  : 'border-line bg-white/[0.03] text-mist hover:border-mist/40 hover:text-paper',
              )}
            >
              <Icon size={14} />
              {provider.label}
            </button>
          )
        })}
      </div>

      {providers.isError ? (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {errorMessage(providers.error, '加载来源失败')}
        </div>
      ) : null}

      {current ? (
        <div className="rounded-2xl border border-line bg-panel/60 p-4 sm:p-5">
          <p className="mb-3 text-xs text-mist">{current.description}</p>
          {Panel ? <Panel api={api} onQueued={refreshCache} /> : <div className="text-sm text-mist">该来源暂未实现</div>}
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-medium text-paper">
            <Archive size={15} className="text-signal" /> 已缓存
            <span className="text-xs font-normal text-mist">
              {filtered.length}
              {filtered.length !== items.length ? ` / ${items.length}` : ''} 项
              {cacheQuery.data ? ` · ${formatBytes(cacheQuery.data.total_bytes)}` : ''}
            </span>
          </h2>
          <Button type="button" variant="line" onClick={refreshCache}>
            {cacheQuery.isFetching ? <RefreshCw size={14} className="animate-spin" /> : <RefreshCw size={14} />} 刷新
          </Button>
        </div>

        {items.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setProviderFilter('all')}
              className={cn(
                'rounded-full border px-2.5 py-1 text-xs transition',
                providerFilter === 'all'
                  ? 'border-signal/40 bg-signal/15 text-signal'
                  : 'border-line text-mist hover:border-mist/40 hover:text-paper',
              )}
            >
              全部
            </button>
            {list.map((provider) => {
              const count = items.filter((row) => row.provider === provider.slug).length
              if (!count) return null
              return (
                <button
                  key={provider.slug}
                  type="button"
                  onClick={() => setProviderFilter(provider.slug)}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-xs transition',
                    providerFilter === provider.slug
                      ? 'border-signal/40 bg-signal/15 text-signal'
                      : 'border-line text-mist hover:border-mist/40 hover:text-paper',
                  )}
                >
                  {provider.short_label || provider.label}
                  <span className="ml-1 text-[10px] text-mist">{count}</span>
                </button>
              )
            })}
            <div className="ml-auto flex items-center gap-2">
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索名称 / 文件名"
                className="h-8 w-40 rounded-lg border border-line bg-ink/60 px-2.5 text-xs text-paper outline-none transition placeholder:text-mist/60 focus:border-signal/50"
              />
              <select
                value={sort}
                onChange={(event) => setSort(event.target.value as 'recent' | 'name' | 'size')}
                className="h-8 rounded-lg border border-line bg-ink/60 px-2 text-xs text-paper outline-none transition focus:border-signal/50"
              >
                <option value="recent">最近缓存</option>
                <option value="name">按名称</option>
                <option value="size">按大小</option>
              </select>
            </div>
          </div>
        ) : null}

        {filtered.length ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filtered.map((row) => (
              <CacheCard key={row.id} row={row} api={api} canManage={canManage} onChanged={refreshCache} />
            ))}
          </div>
        ) : items.length ? (
          <EmptyNote message="没有符合筛选条件的缓存" />
        ) : (
          <EmptyNote message="还没有缓存记录，下载一次后服务器会留一份，之后可在此直接获取" />
        )}
      </div>
    </div>
  )
}
