import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Heart, ImageOff, Play, Plus, Search, Send } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SyntheticEvent } from 'react'
import { Link, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { InfoMasonry } from '../components/InfoMasonry'
import { InfoTextCover } from '../components/InfoTextCover'
import { Button, Input, Select } from '../components/ui'
import { api, type InfoItem } from '../lib/api'
import {
  formatCount,
  formatDuration,
  infoItemRatio,
  patchInfoItemCaches,
  relativeTime,
  removeInfoItemFromCaches,
} from '../lib/info'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

const PAGE_SIZE = 24
/** 卡片预估高度里的固定部分：标题两行 + 页脚一行 */
const CARD_TITLE_HEIGHT = 40
const CARD_FOOTER_HEIGHT = 42
const SKELETON_RATIOS = [3 / 4, 1, 4 / 5, 4 / 5, 3 / 4, 1]

const KIND_FILTERS = [
  { value: 'all', label: '全部' },
  // 后端支持逗号分隔的多类型：图文 = image,mixed
  { value: 'image,mixed', label: '图文' },
  { value: 'video', label: '视频' },
  { value: 'text', label: '纯文字' },
]

const CHIP_CLASS =
  'inline-flex shrink-0 items-center gap-1 rounded-full border px-3 py-2 text-xs leading-5 transition'
const CHIP_IDLE = 'border-line bg-white/[0.03] text-mist hover:border-mist/40 hover:text-paper'
const CHIP_ACTIVE = 'border-signal/40 bg-signal/15 text-signal'

type FeedEntry =
  | { kind: 'item'; key: string; item: InfoItem; ratio: number }
  | { kind: 'skeleton'; key: string; item: null; ratio: number }

export function InfoFeedPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()

  const sourceId = params.get('source_id') || ''
  const kind = params.get('kind') || 'all'
  const favoriteOnly = params.get('favorite') === '1'
  const keyword = params.get('q') || ''
  const order = params.get('order') === 'asc' ? 'asc' : 'desc'

  const [search, setSearch] = useState(keyword)
  const [measured, setMeasured] = useState<Record<string, number>>({})
  const sentinel = useRef<HTMLDivElement>(null)

  const stats = useQuery({ queryKey: ['info-stats'], queryFn: api.infoStats })
  const sources = useQuery({ queryKey: ['info-sources'], queryFn: api.infoSources })
  const sourceList = sources.data?.sources ?? []

  const feed = useInfiniteQuery({
    queryKey: ['info-items', { sourceId, kind, favoriteOnly, keyword, order }],
    queryFn: ({ pageParam }) =>
      api.infoItems({
        cursor: pageParam || undefined,
        limit: PAGE_SIZE,
        source_id: sourceId || undefined,
        kind: kind === 'all' ? undefined : kind,
        q: keyword || undefined,
        favorite: favoriteOnly ? 1 : undefined,
        order,
      }),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor || undefined,
  })

  const items = useMemo(
    () => (feed.data?.pages ?? []).flatMap((page) => page.items ?? []),
    [feed.data],
  )
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = feed
  const pageCount = feed.data?.pages.length ?? 0
  const lastPageCount = feed.data?.pages[pageCount - 1]?.items?.length ?? 0

  // 外部（回退/清除筛选）改动了 URL 时同步搜索框
  useEffect(() => {
    setSearch(keyword)
  }, [keyword])

  // 搜索框防抖写入 URL，保证刷新/回退后筛选条件仍在
  useEffect(() => {
    const trimmed = search.trim()
    if (trimmed === keyword) return
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(params)
      if (trimmed) next.set('q', trimmed)
      else next.delete('q')
      setParams(next, { replace: true })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [search, keyword, params, setParams])

  // 底部 sentinel：提前 800px 预取下一页；上一页为空时不再继续，避免死循环请求
  useEffect(() => {
    const node = sentinel.current
    if (!node || !hasNextPage || isFetchingNextPage) return
    if (pageCount > 0 && lastPageCount === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void fetchNextPage()
      },
      { rootMargin: '800px 0px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, pageCount, lastPageCount])

  const favorite = useMutation({
    mutationFn: ({ id, next }: { id: string; next: boolean }) => api.infoItemFavorite(id, next),
    onMutate: ({ id, next }) => {
      patchInfoItemCaches(queryClient, id, { is_favorite: next })
    },
    onSuccess: (updated, variables) => {
      patchInfoItemCaches(queryClient, variables.id, { is_favorite: updated?.is_favorite ?? variables.next })
      if (favoriteOnly && !variables.next) removeInfoItemFromCaches(queryClient, variables.id)
    },
    onError: (caught, variables) => {
      patchInfoItemCaches(queryClient, variables.id, { is_favorite: !variables.next })
      notifyBad(errorMessage(caught, '收藏失败'))
    },
  })

  const collect = useMutation({
    mutationFn: (id: string) => api.infoSourceCollect(id),
    onSuccess: async (result) => {
      if (result.error) notifyBad(`采集失败：${result.error}`)
      else notifyOk(`采集完成：新增 ${result.created} 条，拉取 ${result.fetched} 条`)
      await queryClient.invalidateQueries({ queryKey: ['info-items'] })
      await queryClient.invalidateQueries({ queryKey: ['info-sources'] })
      await queryClient.invalidateQueries({ queryKey: ['info-stats'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '采集失败')),
  })

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  function measure(id: string, ratio: number) {
    if (!Number.isFinite(ratio) || ratio <= 0) return
    setMeasured((previous) => {
      const current = previous[id]
      if (current !== undefined && Math.abs(current - ratio) < 0.01) return previous
      return { ...previous, [id]: ratio }
    })
  }

  const entries = useMemo(() => {
    const list: FeedEntry[] = items.map((item) => ({
      kind: 'item',
      key: item.id,
      item,
      ratio: measured[item.id] ?? infoItemRatio(item),
    }))
    const skeletonCount = feed.isLoading ? 8 : isFetchingNextPage ? 6 : 0
    for (let index = 0; index < skeletonCount; index += 1) {
      list.push({
        kind: 'skeleton',
        key: `skeleton-${index}`,
        item: null,
        ratio: SKELETON_RATIOS[index % SKELETON_RATIOS.length],
      })
    }
    return list
  }, [items, measured, feed.isLoading, isFetchingNextPage])

  const filtersActive = Boolean(sourceId || keyword || favoriteOnly || kind !== 'all')
  const filteredTotal = feed.data?.pages[0]?.total ?? 0
  const noSources = !sources.isLoading && sourceList.length === 0
  const showEmpty = !feed.isLoading && items.length === 0 && !feed.isError

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1280px] space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold">资讯收集</h2>
          <p className="mt-1 text-xs text-mist">
            {stats.isLoading
              ? '统计加载中…'
              : `总 ${formatCount(stats.data?.item_count ?? 0)} 条 · ${stats.data?.source_count ?? 0} 个渠道`}
            {filtersActive ? ` · 筛选后 ${formatCount(filteredTotal)} 条` : ''}
          </p>
        </div>
        <Button type="button" onClick={() => navigate('/info/sources')}>
          <Plus size={15} /> 添加渠道
        </Button>
      </div>

      <div className="sticky top-[calc(var(--app-header)+env(safe-area-inset-top))] z-20 -mx-4 border-y border-line bg-ink/95 px-4 py-2 backdrop-blur-md lg:top-0 lg:mx-0 lg:rounded-lg lg:border lg:px-3 lg:py-2.5">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-2">
          <Select
            aria-label="渠道筛选"
            className="w-full lg:w-40 lg:shrink-0"
            value={sourceId}
            onChange={(event) => setParam('source_id', event.target.value)}
          >
            <option value="">全部渠道</option>
            {sourceList.map((source) => (
              <option key={source.id} value={source.id}>
                {source.title || source.username || source.identifier}
              </option>
            ))}
          </Select>

          <div className="-mx-1 flex min-w-0 items-center gap-1.5 overflow-x-auto px-1 lg:contents">
            {KIND_FILTERS.map((filter) => {
              const active = kind === filter.value
              return (
                <button
                  key={filter.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setParam('kind', filter.value === 'all' ? '' : filter.value)}
                  className={cn(CHIP_CLASS, active ? CHIP_ACTIVE : CHIP_IDLE)}
                >
                  {filter.label}
                </button>
              )
            })}
          </div>

          <div className="flex min-w-0 items-center gap-2 lg:contents">
            <button
              type="button"
              aria-pressed={favoriteOnly}
              onClick={() => setParam('favorite', favoriteOnly ? '' : '1')}
              className={cn(CHIP_CLASS, favoriteOnly ? CHIP_ACTIVE : CHIP_IDLE)}
            >
              <Heart size={13} className={cn(favoriteOnly && 'fill-current')} /> 收藏
            </button>

            <div className="relative min-w-0 flex-1 lg:w-52 lg:flex-none">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
              <Input
                className="h-9 py-1.5 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索内容"
                aria-label="搜索内容"
              />
            </div>

            <Select
              aria-label="排序"
              className="w-24 shrink-0"
              value={order}
              onChange={(event) => setParam('order', event.target.value === 'asc' ? 'asc' : '')}
            >
              <option value="desc">最新</option>
              <option value="asc">最早</option>
            </Select>
          </div>
        </div>
      </div>

      {feed.isError ? (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {errorMessage(feed.error, '加载内容失败')}
        </div>
      ) : null}

      {showEmpty ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-14 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-line bg-panel text-mist">
            <ImageOff size={20} />
          </div>
          <p className="mt-3 text-sm text-mist">
            {noSources ? '还没有采集渠道，先添加一个 Telegram 频道' : filtersActive ? '没有符合条件的内容' : '该渠道暂无可展示内容'}
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {noSources ? (
              <Button type="button" onClick={() => navigate('/info/sources')}>
                <Plus size={15} /> 添加 Telegram 频道
              </Button>
            ) : null}
            {!noSources && sourceId ? (
              <Button
                type="button"
                disabled={collect.isPending}
                onClick={() => collect.mutate(sourceId)}
              >
                <Send size={15} /> {collect.isPending ? '采集中…' : '立即采集'}
              </Button>
            ) : null}
            {!noSources && filtersActive ? (
              <Button
                type="button"
                variant="line"
                onClick={() => {
                  setSearch('')
                  setParams(new URLSearchParams(), { replace: true })
                }}
              >
                清除筛选
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <InfoMasonry
          items={entries}
          itemKey={(entry) => entry.key}
          estimateHeight={(entry, columnWidth) =>
            columnWidth / entry.ratio +
            (entry.kind === 'item' && entry.item.cover ? CARD_TITLE_HEIGHT : 0) +
            CARD_FOOTER_HEIGHT
          }
          renderItem={(entry) =>
            entry.kind === 'skeleton' ? (
              <SkeletonCard ratio={entry.ratio} />
            ) : (
              <InfoCard
                item={entry.item}
                ratio={entry.ratio}
                onMeasure={measure}
                onToggleFavorite={(target) =>
                  favorite.mutate({ id: target.id, next: !target.is_favorite })
                }
              />
            )
          }
        />
      )}

      <div ref={sentinel} className="h-px w-full" aria-hidden="true" />

      {isFetchingNextPage ? (
        <div className="flex items-center justify-center gap-2 py-3 text-xs text-mist">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-signal/20 border-t-signal" />
          正在加载更多…
        </div>
      ) : null}
      {!hasNextPage && items.length > 0 ? (
        <div className="py-3 text-center text-xs text-mist">已经到底了</div>
      ) : null}

      {/* 详情浮层：作为子路由渲染在瀑布流之上，返回时可保留列表状态 */}
      <Outlet />
    </div>
  )
}

function SkeletonCard({ ratio }: { ratio: number }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel">
      <div className="w-full animate-pulse bg-panel-2" style={{ aspectRatio: `${ratio}` }} />
      <div className="space-y-2 p-3">
        <div className="h-3 w-4/5 animate-pulse rounded bg-panel-2" />
        <div className="h-3 w-2/5 animate-pulse rounded bg-panel-2" />
      </div>
    </div>
  )
}

function ChannelAvatar({ name, url, size = 18 }: { name: string; url: string; size?: number }) {
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
  }, [url])

  const initial = (name || '?').trim().slice(0, 1).toUpperCase() || '?'
  if (!url || failed) {
    return (
      <span
        aria-hidden="true"
        className="flex shrink-0 items-center justify-center rounded-full bg-panel-2 text-[10px] text-mist"
        style={{ width: size, height: size }}
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
      className="shrink-0 rounded-full bg-panel-2 object-cover"
      style={{ width: size, height: size }}
    />
  )
}

function InfoCard({
  item,
  ratio,
  onMeasure,
  onToggleFavorite,
}: {
  item: InfoItem
  ratio: number
  onMeasure: (id: string, ratio: number) => void
  onToggleFavorite: (item: InfoItem) => void
}) {
  const [failed, setFailed] = useState(false)
  const [preview, setPreview] = useState(false)

  useEffect(() => {
    setFailed(false)
    setPreview(false)
  }, [item.id])

  const cover = item.cover
  const image = cover && !failed ? cover : null
  const channel = item.source?.title || item.author_name || item.source?.username || '未命名频道'
  const avatar = item.source?.avatar_url || ''
  const time = relativeTime(item.published_at || item.collected_at)
  const excerpt = item.excerpt || item.text || ''
  const isVideo = item.kind === 'video' || item.kind === 'mixed'
  const duration = formatDuration(cover?.duration_ms)
  // 列表里视频条目的封面是 poster，可播放地址由后端在 cover.video_url 给出；
  // 只有 hover 时才会挂载 <video>，因此不会预加载视频字节。
  const previewSrc = cover?.video_url || (cover?.kind === 'video' ? cover.url : '')
  const previewable = Boolean(isVideo && previewSrc && !failed)

  function handleLoad(event: SyntheticEvent<HTMLImageElement>) {
    const node = event.currentTarget
    if (node.naturalWidth > 0 && node.naturalHeight > 0) {
      onMeasure(item.id, node.naturalWidth / node.naturalHeight)
    }
  }

  function startPreview() {
    if (!previewable) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    setPreview(true)
  }

  return (
    <article className="group overflow-hidden rounded-xl border border-line bg-panel transition [contain-intrinsic-size:auto_420px] [content-visibility:auto] hover:border-signal/30 motion-reduce:translate-none motion-reduce:transform-none lg:hover:-translate-y-0.5">
      <Link
        to={`/info/${item.id}`}
        className="block"
        onMouseEnter={startPreview}
        onMouseLeave={() => setPreview(false)}
      >
        <div
          className="relative w-full overflow-hidden bg-ink"
          style={image ? { aspectRatio: `${ratio}` } : undefined}
        >
          {image ? (
            <img
              src={image.url}
              alt=""
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
              onLoad={handleLoad}
              onError={() => setFailed(true)}
              className="h-full w-full object-cover"
            />
          ) : (
            <InfoTextCover seed={item.cover_seed} text={excerpt} channel={channel} time={time} />
          )}

          {isVideo && image ? (
            <span className="pointer-events-none absolute left-1/2 top-1/2 flex h-9 w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-black/50 text-paper backdrop-blur-sm">
              <Play size={16} className="translate-x-[1px] fill-current" />
            </span>
          ) : null}

          {duration && image ? (
            <span className="pointer-events-none absolute bottom-1.5 right-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[11px] text-paper">
              {duration}
            </span>
          ) : null}

          {image && item.media_count > 1 && !isVideo ? (
            <span className="pointer-events-none absolute right-1.5 top-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[11px] text-paper">
              1/{item.media_count}
            </span>
          ) : null}

          {!image ? (
            <span className="pointer-events-none absolute left-1.5 top-1.5 rounded bg-black/45 px-1.5 py-0.5 text-[10px] text-paper/80">
              文字
            </span>
          ) : null}

          {preview && previewable ? (
            <video
              ref={(node) => {
                if (node) node.muted = true
              }}
              src={previewSrc}
              className="absolute inset-0 h-full w-full object-cover"
              muted
              autoPlay
              playsInline
              preload="none"
              onError={() => setPreview(false)}
            >
              <track kind="captions" />
            </video>
          ) : null}
        </div>

        {image && excerpt ? (
          <p className="line-clamp-2 px-3 pt-2 text-[13px] leading-5 text-paper">{excerpt}</p>
        ) : null}
        {/* 纯文字卡片的文本已经在封面内（装饰层 aria-hidden），这里补一份可读文本 */}
        {!image && excerpt ? <span className="sr-only">{excerpt}</span> : null}
      </Link>

      <div className="flex items-center gap-2 px-3 py-2">
        <Link
          to={`/info/${item.id}`}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-[11px] text-mist hover:text-paper"
        >
          <ChannelAvatar name={channel} url={avatar} />
          <span className="min-w-0 truncate">
            {channel}
            {time ? ` · ${time}` : ''}
          </span>
        </Link>
        <button
          type="button"
          aria-label={item.is_favorite ? '取消收藏' : '收藏'}
          aria-pressed={item.is_favorite}
          onClick={() => onToggleFavorite(item)}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-mist transition hover:bg-white/5 hover:text-paper"
        >
          <Heart size={15} className={cn(item.is_favorite && 'fill-signal text-signal')} />
        </button>
      </div>
    </article>
  )
}

export default InfoFeedPage
