import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Copy, ExternalLink, Eye, EyeOff, Heart, ImageOff, Sparkles, Star, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { InfoTextCover } from '../components/InfoTextCover'
import { Badge, Button } from '../components/ui'
import { api, type InfoItemDetail, type InfoMedia } from '../lib/api'
import {
  formatCount,
  infoItemRatio,
  infoKindLabel,
  infoMediaRatio,
  patchInfoItemCaches,
  relativeTime,
  removeInfoItemFromCaches,
} from '../lib/info'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, copyText, errorMessage, formatTime } from '../lib/utils'

const LINE_BUTTON =
  'inline-flex min-h-10 items-center justify-center gap-1.5 rounded-md border border-line bg-panel-2 px-3 py-2 text-xs text-paper transition hover:border-mist/40'

/** 详情浮层：覆盖在瀑布流之上，路由切换驱动，支持浏览器/手势返回 */
export function InfoItemPage() {
  const { itemId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['info-item', itemId],
    queryFn: () => api.infoItem(itemId),
    enabled: Boolean(itemId),
  })
  const item = query.data
  const media = useMemo(() => (item?.media ?? []).filter((entry) => entry.kind !== 'poster'), [item])

  const [index, setIndex] = useState(0)
  const [broken, setBroken] = useState<Record<string, boolean>>({})
  const [dragX, setDragX] = useState(0)
  const [dragging, setDragging] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)
  const mediaRef = useRef<HTMLDivElement>(null)

  const safeIndex = media.length ? Math.min(index, media.length - 1) : 0
  const current = media[safeIndex] ?? null

  const close = useCallback(() => {
    if (location.key && location.key !== 'default') navigate(-1)
    else navigate(`/info${location.search}`, { replace: true })
  }, [location.key, location.search, navigate])

  const step = useCallback(
    (delta: number) => {
      setIndex((value) => {
        if (!media.length) return 0
        const base = Math.min(value, media.length - 1)
        return (base + delta + media.length) % media.length
      })
    },
    [media.length],
  )

  // 手势：右滑跟随退出；左滑切下一张媒体。用原生 touch 监听并在横滑锁定时
  // preventDefault，阻止原生滚动接管（滚动会触发 pointercancel 把手势打断），
  // 这是 react-swipeable / framer-motion 等库处理“拖拽 vs 滚动”冲突的通用做法。
  useEffect(() => {
    const node = overlayRef.current
    if (!node) return
    let active = false
    let locked = false
    let dragging = false
    let inMedia = false
    let startX = 0
    let startY = 0

    function onTouchStart(event: TouchEvent) {
      if (event.touches.length !== 1) {
        active = false
        return
      }
      const touch = event.touches[0]
      const target = event.target
      if (
        target instanceof Element &&
        target.closest('button, a, input, textarea, select, video, [contenteditable="true"]')
      ) {
        active = false
        return
      }
      active = true
      locked = false
      dragging = false
      startX = touch.clientX
      startY = touch.clientY
      inMedia = Boolean(mediaRef.current && target instanceof Node && mediaRef.current.contains(target))
    }

    function onTouchMove(event: TouchEvent) {
      if (!active || event.touches.length !== 1) return
      const touch = event.touches[0]
      const dx = touch.clientX - startX
      const dy = touch.clientY - startY
      if (!locked) {
        const absX = Math.abs(dx)
        const absY = Math.abs(dy)
        if (absX < 10 && absY < 10) return
        // 垂直分量一旦不输于水平，直接让位给滚动，不做横滑判定
        if (absY >= absX) {
          active = false
          return
        }
        // 只有横向明显占优（1.5 倍且超过最小距离）才锁定为横滑，
        // 否则继续保持中立，交给浏览器滚动
        if (absX <= absY * 1.5 || absX < 16) return
        locked = true
      }
      // 横向锁定后阻止原生滚动，保证拖拽过程不被打断
      if (event.cancelable) event.preventDefault()
      if (dx <= 0) return
      dragging = true
      setDragging(true)
      setDragX(Math.max(0, dx))
    }

    function onTouchEnd(event: TouchEvent) {
      const wasDragging = dragging
      const mediaNav = inMedia && media.length > 1
      const touch = event.changedTouches[0]
      const dx = (touch ? touch.clientX : startX) - startX
      const dy = touch ? touch.clientY - startY : 0
      active = false
      locked = false
      dragging = false
      if (wasDragging) {
        const finalX = Math.max(0, dx)
        const threshold = Math.min(120, window.innerWidth * 0.25)
        setDragging(false)
        if (finalX > threshold || (finalX > 64 && dx > 0)) {
          // 先滑出再关闭，形成退场动效
          setDragX(window.innerWidth)
          window.setTimeout(close, 200)
        } else {
          setDragX(0)
        }
        return
      }
      // 左滑切下一张；右滑始终是退出，不与媒体切换竞争
      if (dx <= -48 && Math.abs(dx) > Math.abs(dy) && mediaNav) step(1)
    }

    function onTouchCancel() {
      active = false
      locked = false
      dragging = false
      setDragging(false)
      setDragX(0)
    }

    node.addEventListener('touchstart', onTouchStart, { passive: true })
    node.addEventListener('touchmove', onTouchMove, { passive: false })
    node.addEventListener('touchend', onTouchEnd)
    node.addEventListener('touchcancel', onTouchCancel)
    return () => {
      node.removeEventListener('touchstart', onTouchStart)
      node.removeEventListener('touchmove', onTouchMove)
      node.removeEventListener('touchend', onTouchEnd)
      node.removeEventListener('touchcancel', onTouchCancel)
    }
  }, [close, media.length, step])

  // ESC 关闭、←/→ 切换媒体
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        close()
        return
      }
      if (event.key === 'ArrowLeft') step(-1)
      if (event.key === 'ArrowRight') step(1)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [close, step])

  // 浮层打开时锁住背景滚动
  useEffect(() => {
    const body = document.body
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previous
    }
  }, [])

  const favorite = useMutation({
    mutationFn: (next: boolean) => api.infoItemFavorite(itemId, next),
    onSuccess: (updated, next) => {
      patchInfoItemCaches(queryClient, itemId, { is_favorite: updated?.is_favorite ?? next })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '操作失败')),
  })

  const hidden = useMutation({
    mutationFn: (next: boolean) => api.infoItemHidden(itemId, next),
    onSuccess: (updated, next) => {
      patchInfoItemCaches(queryClient, itemId, { is_hidden: updated?.is_hidden ?? next })
      if (next) {
        removeInfoItemFromCaches(queryClient, itemId)
        notifyOk('已隐藏该内容')
      } else {
        notifyOk('已恢复显示')
      }
    },
    onError: (caught) => notifyBad(errorMessage(caught, '操作失败')),
  })

  const featured = useMutation({
    mutationFn: (next: boolean) => api.infoItemFeatured(itemId, next),
    onSuccess: (updated, next) => {
      const value = updated?.is_featured ?? next
      patchInfoItemCaches(queryClient, itemId, { is_featured: value })
      notifyOk(value ? '已加入精选' : '已取消精选')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '操作失败')),
  })

  function markBroken(id: string) {
    setBroken((previous) => ({ ...previous, [id]: true }))
  }

  return (
    <div
      ref={overlayRef}
      data-swipe-overlay
      style={{
        transform: dragX ? `translateX(${dragX}px)` : undefined,
        transition: dragging ? 'none' : 'transform 200ms cubic-bezier(0.22, 1, 0.36, 1)',
        touchAction: 'pan-y',
      }}
      className="fixed inset-0 z-40 flex flex-col bg-ink/95 backdrop-blur-sm"
    >
      <div className="flex items-center justify-between gap-2 border-b border-line bg-panel/95 px-2 pt-[env(safe-area-inset-top)]">
        <button
          type="button"
          onClick={close}
          aria-label="关闭"
          className="inline-flex min-h-11 min-w-11 items-center justify-center text-mist transition hover:text-paper"
        >
          <X size={18} />
        </button>
        <div className="flex min-w-0 items-center gap-2">
          {item ? (
            <span className="hidden min-w-0 truncate text-xs text-mist sm:block">
              {item.source?.title || item.author_name}
              {item.media_count > 1 ? ` · ${media.length} 项媒体` : ''}
            </span>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            disabled={!item || favorite.isPending}
            onClick={() => favorite.mutate(!(item?.is_favorite ?? false))}
            className={cn('min-h-9 px-2 py-1 text-xs', item?.is_favorite && 'text-signal')}
          >
            <Heart size={14} className={cn(item?.is_favorite && 'fill-signal')} />
            {item?.is_favorite ? '已收藏' : '收藏'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={!item || featured.isPending}
            onClick={() => featured.mutate(!(item?.is_featured ?? false))}
            className={cn('min-h-9 px-2 py-1 text-xs', item?.is_featured && 'text-amber-300')}
          >
            <Star size={14} className={cn(item?.is_featured && 'fill-amber-300')} />
            {item?.is_featured ? '已精选' : '精选'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={!item || hidden.isPending}
            onClick={() => hidden.mutate(!(item?.is_hidden ?? false))}
            className="min-h-9 px-2 py-1 text-xs"
          >
            {item?.is_hidden ? <Eye size={14} /> : <EyeOff size={14} />}
            {item?.is_hidden ? '取消隐藏' : '隐藏'}
          </Button>
        </div>
      </div>

      {query.isLoading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-mist">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-signal/20 border-t-signal" />
          加载中…
        </div>
      ) : null}

      {!query.isLoading && (!item || query.isError) ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <ImageOff size={22} className="text-mist" />
          <div className="text-sm text-danger">{errorMessage(query.error, '内容不存在或已被清理')}</div>
          <Button type="button" variant="line" onClick={close}>
            返回列表
          </Button>
        </div>
      ) : null}

      {item ? (
        <div
          className={cn(
            'flex min-h-0 flex-1 flex-col overflow-y-auto lg:gap-4 lg:p-4',
            item.content_html
              ? 'items-center'
              : 'lg:grid lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)] lg:overflow-hidden',
          )}
        >
          {item.content_html ? null : (
          <div className="relative flex min-h-0 flex-col bg-black/40 lg:h-full lg:rounded-lg lg:border lg:border-line">
            <div
              ref={mediaRef}
              className="relative flex w-full items-center justify-center overflow-hidden bg-ink/80 lg:h-full lg:min-h-0 lg:aspect-auto"
              style={{ aspectRatio: `${mediaRatio(current, item)}` }}
            >
              <MediaStage
                item={item}
                media={current}
                broken={Boolean(current && broken[current.id])}
                onBroken={markBroken}
                channel={item.source?.title || item.author_name || '未命名频道'}
                time={relativeTime(item.published_at || item.collected_at)}
              />

              {media.length > 1 ? (
                <>
                  <button
                    type="button"
                    aria-label="上一项"
                    onClick={() => step(-1)}
                    className="absolute left-2 top-1/2 inline-flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/50 text-paper transition hover:bg-black/70"
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <button
                    type="button"
                    aria-label="下一项"
                    onClick={() => step(1)}
                    className="absolute right-2 top-1/2 inline-flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/50 text-paper transition hover:bg-black/70"
                  >
                    <ChevronRight size={18} />
                  </button>
                  <span className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] text-paper">
                    {safeIndex + 1}/{media.length}
                  </span>
                </>
              ) : null}

              {item.status === 'media_partial' || current?.status === 'failed' ? (
                <span className="pointer-events-none absolute left-2 top-2 rounded bg-danger/80 px-1.5 py-0.5 text-[11px] text-paper">
                  媒体转存失败
                </span>
              ) : null}
            </div>

            {media.length > 1 ? (
              <div className="flex flex-wrap items-center justify-center gap-1.5 px-3 py-2.5">
                {media.map((entry, position) => (
                  <button
                    key={entry.id}
                    type="button"
                    aria-label={`第 ${position + 1} 项`}
                    aria-current={position === safeIndex}
                    onClick={() => setIndex(position)}
                    className="group inline-flex h-6 items-center px-0.5"
                  >
                    <span
                      className={cn(
                        'h-1.5 rounded-full transition-all',
                        position === safeIndex ? 'w-5 bg-signal' : 'w-1.5 bg-white/25 group-hover:bg-white/45',
                      )}
                    />
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          )}

          <div
            className={cn(
              'min-h-0 space-y-3 px-4 pb-6 pt-3 lg:px-1 lg:pb-2',
              item.content_html ? 'w-full max-w-[760px]' : 'lg:overflow-y-auto',
            )}
          >
            <div className="flex items-center gap-2">
              <ChannelAvatar name={item.source?.title || item.author_name || ''} url={item.source?.avatar_url || ''} />
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-paper">
                  {item.source?.title || item.author_name || '未命名频道'}
                </div>
                <div className="truncate text-[11px] text-mist">
                  {item.source?.username ? `@${item.source.username}` : item.author_name || 'Telegram 频道'}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-mist">
              <Badge tone="mist">{infoKindLabel(item.kind)}</Badge>
              <span>{formatTime(item.published_at)}</span>
              {item.published_at ? <span>{relativeTime(item.published_at)}</span> : null}
              {item.views_text || item.views != null ? (
                <span>阅读 {item.views_text || formatCount(item.views)}</span>
              ) : null}
              {item.is_forwarded ? <span className="text-warn">转发内容</span> : null}
            </div>

            {item.ai_status !== 'pending' || item.ai_score != null || item.ai_label ? (
              <div className="rounded-lg border border-line bg-panel-2 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="inline-flex items-center gap-1 text-paper">
                    <Sparkles size={13} className="text-signal" /> AI 判定
                  </span>
                  {item.is_featured ? <Badge tone="ok">精选</Badge> : null}
                  {item.ai_score != null ? <Badge tone="info">{item.ai_score} 分</Badge> : null}
                  {item.ai_label ? (
                    <Badge tone={item.ai_label === 'ad' ? 'bad' : 'mist'}>{aiLabelText(item.ai_label)}</Badge>
                  ) : null}
                  <span className="text-mist">{aiStatusText(item.ai_status)}</span>
                </div>
                {item.ai_reason ? <p className="mt-2 text-xs text-mist">{item.ai_reason}</p> : null}
                {item.ai_tags.length ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {item.ai_tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full border border-line px-2 py-0.5 text-[11px] text-mist"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>
                ) : null}
                {item.ai_error ? <p className="mt-2 text-xs text-danger">{item.ai_error}</p> : null}
              </div>
            ) : null}

            {item.content_html ? (
              <div
                className="wx-content break-words text-sm leading-6 text-paper"
                dangerouslySetInnerHTML={{ __html: item.content_html }}
              />
            ) : item.text ? (
              <p className="whitespace-pre-wrap break-words text-sm leading-6 text-paper [overflow-wrap:anywhere]">
                {item.text}
              </p>
            ) : (
              <p className="text-sm text-mist">该条内容没有正文</p>
            )}

            {item.reactions && item.reactions.length ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {item.reactions.map((reaction, position) => (
                  <span
                    key={`${reaction.emoji}-${position}`}
                    className="inline-flex items-center gap-1 rounded-full border border-line bg-panel-2 px-2 py-0.5 text-xs text-paper"
                  >
                    <span>{reaction.emoji}</span>
                    <span className="text-mist">{reaction.count}</span>
                  </span>
                ))}
                {item.reactions_total != null ? (
                  <span className="text-[11px] text-mist">共 {formatCount(item.reactions_total)}</span>
                ) : null}
              </div>
            ) : null}

            {item.link_preview?.url ? (
              <a
                href={item.link_preview.url}
                target="_blank"
                rel="noreferrer"
                className="block rounded-lg border border-line bg-panel-2 p-3 transition hover:border-signal/40"
              >
                <div className="text-[11px] text-mist">{item.link_preview.site_name || '链接预览'}</div>
                {item.link_preview.title ? (
                  <div className="mt-0.5 line-clamp-2 text-sm text-paper">{item.link_preview.title}</div>
                ) : null}
                {item.link_preview.description ? (
                  <div className="mt-0.5 line-clamp-2 text-xs text-mist">{item.link_preview.description}</div>
                ) : null}
                <div className="mt-1 truncate text-[11px] text-info">{item.link_preview.url}</div>
              </a>
            ) : null}

            <div className="flex flex-wrap gap-2 pt-1">
              {item.permalink ? (
                <a href={item.permalink} target="_blank" rel="noreferrer" className={LINE_BUTTON}>
                  <ExternalLink size={14} /> 打开原帖
                </a>
              ) : null}
              <button
                type="button"
                className={LINE_BUTTON}
                onClick={() => {
                  const target = item.permalink || `${window.location.origin}/info/${item.id}`
                  void copyText(target)
                    .then(() => notifyOk('链接已复制'))
                    .catch(() => notifyBad('复制失败'))
                }}
              >
                <Copy size={14} /> 复制链接
              </button>
            </div>

            <p className="text-[11px] text-mist/80">
              采集时间：{formatTime(item.collected_at)} · 媒体 {item.media_count} 项
              <span className="hidden lg:inline"> · ESC 关闭 / ←→ 切换媒体</span>
            </p>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function aiLabelText(label: string) {
  if (label === 'ad') return '广告'
  if (label === 'valuable') return '高价值'
  if (label === 'general') return '常规'
  if (label === 'other') return '其他'
  return label
}

function aiStatusText(status: string) {
  if (status === 'pending') return '待判定'
  if (status === 'processing') return '判定中'
  if (status === 'failed') return '判定失败'
  if (status === 'skipped') return '已跳过'
  return '已判定'
}

function mediaRatio(media: InfoMedia | null, item: InfoItemDetail) {
  if (media) return infoMediaRatio(media, item)
  return infoItemRatio(item)
}

function MediaStage({
  item,
  media,
  broken,
  onBroken,
  channel,
  time,
}: {
  item: InfoItemDetail
  media: InfoMedia | null
  broken: boolean
  onBroken: (id: string) => void
  channel: string
  time: string
}) {
  const fallback = (
    <InfoTextCover seed={item.cover_seed} text={item.excerpt || item.text} channel={channel} time={time} className="w-full" />
  )

  if (!media || broken || media.status === 'failed' || !media.url) {
    // 媒体转存失败/已清理：回退到该条目的纯文字封面，不出现破图
    return <div className="w-full">{fallback}</div>
  }

  if (media.kind === 'video') {
    return (
      <video
        key={media.id}
        src={media.url}
        poster={media.poster_url || item.cover?.url || undefined}
        controls
        playsInline
        preload="metadata"
        onError={() => onBroken(media.id)}
        className="h-full max-h-[70vh] w-full bg-black object-contain lg:max-h-full"
      >
        <track kind="captions" />
      </video>
    )
  }

  return (
    <img
      key={media.id}
      src={media.url}
      alt=""
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => onBroken(media.id)}
      className="h-full max-h-[70vh] w-full object-contain lg:max-h-full"
    />
  )
}

function ChannelAvatar({ name, url }: { name: string; url: string }) {
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
  }, [url])

  const initial = (name || '?').trim().slice(0, 1).toUpperCase() || '?'
  if (!url || failed) {
    return (
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-panel-2 text-xs text-mist"
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
      className="h-9 w-9 shrink-0 rounded-full border border-line bg-panel-2 object-cover"
    />
  )
}

export default InfoItemPage
