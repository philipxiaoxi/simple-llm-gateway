import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Fingerprint,
  ImageOff,
  KeyRound,
  List,
  Lock,
  Play,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Star,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SyntheticEvent } from 'react'
import { Link, Outlet, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { InfoMasonry } from '../components/InfoMasonry'
import { InfoTextCover } from '../components/InfoTextCover'
import { Badge, Button } from '../components/ui'
import { ApiError, api, type InfoItem, type InfoMedia, type InfoPublicGateStatus } from '../lib/api'
import {
  formatCount,
  formatDuration,
  infoItemRatio,
  infoKindLabel,
  infoMediaRatio,
  relativeTime,
} from '../lib/info'
import { cn, errorMessage, formatTime } from '../lib/utils'

const PAGE_SIZE = 24
const CARD_TITLE_HEIGHT = 40
const CARD_FOOTER_HEIGHT = 42

const GATE_PARTICLES = [
  { left: '10%', top: '22%', size: 3, delay: '0s', duration: '8s' },
  { left: '24%', top: '70%', size: 2, delay: '-2.5s', duration: '10s' },
  { left: '43%', top: '14%', size: 2, delay: '-5s', duration: '7.5s' },
  { left: '69%', top: '28%', size: 3, delay: '-1.5s', duration: '11s' },
  { left: '84%', top: '62%', size: 2, delay: '-4s', duration: '9s' },
  { left: '60%', top: '84%', size: 2, delay: '-6s', duration: '12s' },
]

/** 门禁氛围背景：渐变网格 + 漂浮光斑 + 旋转光弧 + 扫描线 + 星点。 */
function GateBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-ink">
      <div
        className="absolute inset-0 opacity-80"
        style={{
          background:
            'radial-gradient(60% 55% at 18% 14%, rgba(200,245,66,0.20), transparent 62%),' +
            'radial-gradient(55% 50% at 86% 20%, rgba(110,200,255,0.18), transparent 62%),' +
            'radial-gradient(55% 58% at 72% 92%, rgba(168,120,255,0.16), transparent 62%)',
        }}
      />

      <div className="gate-blob absolute left-[6%] top-[10%] h-72 w-72 rounded-full bg-signal/25 blur-[120px]" />
      <div
        className="gate-blob absolute right-[4%] top-[16%] h-80 w-80 rounded-full bg-info/25 blur-[130px]"
        style={{ animationDelay: '-6s' }}
      />
      <div
        className="gate-blob absolute bottom-[4%] left-1/3 h-72 w-72 rounded-full bg-[#a878ff]/25 blur-[130px]"
        style={{ animationDelay: '-11s' }}
      />

      <div
        className="gate-grid absolute inset-0 opacity-[0.14]"
        style={{
          backgroundImage:
            'linear-gradient(rgba(200,245,66,0.65) 1px, transparent 1px),' +
            'linear-gradient(90deg, rgba(110,200,255,0.55) 1px, transparent 1px)',
          backgroundSize: '80px 80px',
          maskImage: 'radial-gradient(circle at 50% 45%, black, transparent 78%)',
          WebkitMaskImage: 'radial-gradient(circle at 50% 45%, black, transparent 78%)',
        }}
      />

      <div className="absolute left-1/2 top-1/2 h-[44rem] w-[44rem] -translate-x-1/2 -translate-y-1/2 opacity-40">
        <div
          className="gate-ring h-full w-full rounded-full"
          style={{
            background:
              'conic-gradient(from 0deg, transparent 0deg, rgba(200,245,66,0.45) 55deg, transparent 130deg,' +
              ' rgba(110,200,255,0.45) 210deg, transparent 300deg)',
            maskImage:
              'radial-gradient(circle, transparent 57%, black 59%, black 61.5%, transparent 64%)',
            WebkitMaskImage:
              'radial-gradient(circle, transparent 57%, black 59%, black 61.5%, transparent 64%)',
          }}
        />
      </div>

      <div
        className="gate-scan absolute inset-x-0 top-0 h-28"
        style={{
          background:
            'linear-gradient(to bottom, transparent, rgba(200,245,66,0.10), rgba(110,200,255,0.06), transparent)',
        }}
      />

      {GATE_PARTICLES.map((dot, position) => (
        <span
          key={position}
          className={cn(
            'gate-float absolute rounded-full',
            position % 2 === 0 ? 'bg-signal/80' : 'bg-info/80',
          )}
          style={{
            left: dot.left,
            top: dot.top,
            width: dot.size,
            height: dot.size,
            animationDelay: dot.delay,
            animationDuration: dot.duration,
            boxShadow: position % 2 === 0 ? '0 0 12px rgba(200,245,66,0.8)' : '0 0 12px rgba(110,200,255,0.8)',
          }}
        />
      ))}

      <div
        className="absolute inset-0"
        style={{ background: 'radial-gradient(circle at 50% 45%, transparent 28%, rgba(11,13,17,0.82) 100%)' }}
      />
    </div>
  )
}

/** 全页水印：解锁会话的溯源短码，斜向平铺，不可选中、不拦截交互。 */
function PublicInfoWatermark() {
  const gate = useQuery({ queryKey: ['public-info-gate'], queryFn: api.publicInfoGate, retry: false })
  const watermark = gate.data?.watermark
  if (!watermark) return null

  const label = `内部资料 · ${watermark.code}`
  const svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='168'>" +
    "<g fill='rgba(232,237,245,0.08)' font-family='IBM Plex Sans, sans-serif' font-size='15' font-weight='500' text-anchor='middle'>" +
    `<text x='150' y='52'>${label}</text>` +
    `<text x='150' y='116'>${label}</text>` +
    '</g></svg>'
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-[100] select-none overflow-hidden">
      <div
        className="absolute left-1/2 top-1/2 h-[240vmax] w-[240vmax] -translate-x-1/2 -translate-y-1/2 -rotate-[24deg]"
        style={{
          backgroundImage: `url("data:image/svg+xml,${encodeURIComponent(svg)}")`,
          backgroundRepeat: 'repeat',
          backgroundSize: '300px 168px',
        }}
      />
    </div>
  )
}

/** 口令门禁：公开页开启门禁且未解锁时展示。内部资料，仅授权人员可进。 */
export function PublicInfoGate({
  onUnlocked,
  title = '内部资讯精选',
}: {
  onUnlocked: () => void
  title?: string
}) {
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reveal, setReveal] = useState(false)

  async function submit(event: SyntheticEvent) {
    event.preventDefault()
    if (!password.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      const result = await api.publicInfoUnlock(password)
      const code = result.watermark?.code ?? null
      // 立即写入门禁缓存，水印无需刷新即可显示
      queryClient.setQueryData<InfoPublicGateStatus>(['public-info-gate'], (prev) => ({
        required: true,
        unlocked: true,
        watermark: code ? { code, issued_at: null } : (prev?.watermark ?? null),
      }))
      setPassword('')
      onUnlocked()
    } catch (caught) {
      setError(errorMessage(caught, '口令错误'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-enter relative w-full max-w-md">
      <GateBackdrop />

      <div className="relative z-10 overflow-hidden rounded-[28px] border border-line/80 bg-panel/80 shadow-[0_40px_120px_-30px_rgba(0,0,0,0.95)] backdrop-blur-xl">
        <div className="h-px w-full bg-gradient-to-r from-transparent via-signal/70 to-transparent" />
        <div className="space-y-6 p-7 sm:p-9">
          <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.34em] text-signal/90">
            <Lock size={13} /> RESTRICTED · INTERNAL
          </div>

          <div className="flex items-center gap-4">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-signal/30 bg-gradient-to-br from-signal/25 via-signal/10 to-info/10 text-signal shadow-[0_0_30px_-8px_rgba(200,245,66,0.5)]">
              <ShieldCheck size={27} />
            </div>
            <div className="min-w-0">
              <h1 className="text-xl font-semibold tracking-wide text-paper">{title}</h1>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.2em] text-mist">
                Internal Intelligence Feed
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-xl border border-warn/25 bg-warn/[0.06] px-3.5 py-3">
            <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warn" />
            <p className="text-xs leading-5 text-warn/90">
              本页内容为内部资料，仅供内部人员使用，请勿截图、转发或对外传播。
            </p>
          </div>

          <form onSubmit={submit} className="space-y-3">
            <div className="relative">
              <KeyRound size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-mist" />
              <input
                type={reveal ? 'text' : 'password'}
                value={password}
                autoComplete="current-password"
                placeholder="请输入访问口令"
                onChange={(event) => setPassword(event.target.value)}
                disabled={busy}
                className="h-12 w-full rounded-xl border border-line bg-ink/60 pl-10 pr-11 text-sm text-paper outline-none transition placeholder:text-mist/60 focus:border-signal/60 focus:ring-2 focus:ring-signal/20 disabled:opacity-60"
              />
              <button
                type="button"
                aria-label={reveal ? '隐藏口令' : '显示口令'}
                onClick={() => setReveal((value) => !value)}
                disabled={busy}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-mist transition hover:text-paper disabled:opacity-50"
              >
                {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>

            {error ? (
              <div className="rounded-lg border border-danger/25 bg-danger/[0.08] px-3 py-2 text-xs text-danger">
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={busy || !password.trim()}
              className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-signal text-sm font-semibold text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? (
                '验证中…'
              ) : (
                <>
                  <Fingerprint size={17} /> 验证并进入
                </>
              )}
            </button>
          </form>

          <p className="text-center text-[11px] leading-5 text-mist/70">请勿在公共设备上保持登录</p>
        </div>
      </div>
    </div>
  )
}

/** 公开资讯页：无需登录，默认只看精选，可切换全部；不展示渠道来源。 */
export function PublicInfoPage() {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const queryClient = useQueryClient()
  const showAll = params.get('all') === '1'
  const detailSearch = location.search

  const gate = useQuery({ queryKey: ['public-info-gate'], queryFn: api.publicInfoGate, retry: false })
  const gateOpen = Boolean(gate.data && (!gate.data.required || gate.data.unlocked))

  const stats = useQuery({
    queryKey: ['public-info-stats'],
    queryFn: api.publicInfoStats,
    enabled: gateOpen,
  })
  const feed = useInfiniteQuery({
    queryKey: ['public-info', showAll],
    initialPageParam: '',
    enabled: gateOpen,
    queryFn: ({ pageParam }) =>
      api.publicInfoItems({
        cursor: pageParam || undefined,
        limit: PAGE_SIZE,
        order: 'desc',
        featured: showAll ? 0 : undefined,
      }),
    getNextPageParam: (last) => last.next_cursor || undefined,
  })
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = feed
  const pageCount = feed.data?.pages.length ?? 0
  const lastPageCount = feed.data?.pages[pageCount - 1]?.items?.length ?? 0

  const items = useMemo(() => (feed.data?.pages ?? []).flatMap((page) => page.items), [feed.data])
  const [measured, setMeasured] = useState<Record<string, number>>({})
  const sentinel = useRef<HTMLDivElement>(null)

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

  function measure(id: string, ratio: number) {
    setMeasured((prev) => (prev[id] === ratio ? prev : { ...prev, [id]: ratio }))
  }

  function toggleAll() {
    const next = new URLSearchParams(params)
    if (showAll) next.delete('all')
    else next.set('all', '1')
    setParams(next, { replace: true })
  }

  function handleUnlocked() {
    void queryClient.invalidateQueries({ queryKey: ['public-info'] })
    void queryClient.invalidateQueries({ queryKey: ['public-info-stats'] })
  }

  async function lockNow() {
    try {
      await api.publicInfoLock()
    } catch {
      /* 忽略：本地状态照常清空 */
    }
    queryClient.setQueryData<InfoPublicGateStatus>(['public-info-gate'], {
      required: true,
      unlocked: false,
      watermark: null,
    })
    queryClient.removeQueries({ queryKey: ['public-info'] })
    queryClient.removeQueries({ queryKey: ['public-info-stats'] })
  }

  if (gate.isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center bg-ink text-sm text-mist">加载中…</div>
    )
  }

  if (gate.data?.required && !gate.data.unlocked) {
    return (
      <div className="page-enter flex min-h-svh items-center justify-center bg-ink px-4">
        <PublicInfoGate onUnlocked={handleUnlocked} />
      </div>
    )
  }

  const entries = items.map((item) => ({ item, ratio: measured[item.id] || infoItemRatio(item) }))

  return (
    <div className="page-enter min-h-svh bg-ink px-4 pt-[max(2rem,calc(env(safe-area-inset-top)+1.25rem))] pb-[max(3rem,calc(env(safe-area-inset-bottom)+2rem))]">
      <div className="mx-auto w-full max-w-[1280px] space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-xs tracking-[0.28em] text-signal">AI FEED</div>
            <h1 className="mt-2 text-2xl font-semibold">资讯精选</h1>
            <p className="mt-1 text-xs text-mist">
              {stats.isLoading
                ? '加载中…'
                : `精选 ${formatCount(stats.data?.featured_count ?? 0)} 条 · 共 ${formatCount(stats.data?.item_count ?? 0)} 条`}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {gate.data?.required ? (
              <Button type="button" variant="ghost" onClick={lockNow}>
                <Lock size={15} /> 退出
              </Button>
            ) : null}
            <Button type="button" variant="line" onClick={toggleAll}>
              {showAll ? (
                <>
                  <Star size={15} /> 只看精选
                </>
              ) : (
                <>
                  <List size={15} /> 显示全部
                </>
              )}
            </Button>
          </div>
        </div>

        {feed.isError ? (
          <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {errorMessage(feed.error, '加载内容失败')}
          </div>
        ) : null}

        {!feed.isLoading && !feed.isError && items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-14 text-center text-sm text-mist">
            {showAll ? '暂无内容' : '暂无精选内容'}
          </div>
        ) : null}

        {feed.isLoading ? <div className="py-10 text-center text-sm text-mist">加载中…</div> : null}

        {entries.length > 0 ? (
          <InfoMasonry
            items={entries}
            itemKey={(entry) => entry.item.id}
            estimateHeight={(entry, columnWidth) =>
              columnWidth / entry.ratio +
              (entry.item.cover ? CARD_TITLE_HEIGHT : 0) +
              CARD_FOOTER_HEIGHT
            }
            renderItem={(entry) => (
              <PublicInfoCard
                item={entry.item}
                ratio={entry.ratio}
                search={detailSearch}
                onMeasure={measure}
              />
            )}
          />
        ) : null}

        <div ref={sentinel} className="h-px w-full" aria-hidden="true" />
        {isFetchingNextPage ? (
          <div className="py-3 text-center text-xs text-mist">正在加载更多…</div>
        ) : null}
      </div>

      <Outlet />
      <PublicInfoWatermark />
    </div>
  )
}

function PublicInfoCard({
  item,
  ratio,
  search,
  onMeasure,
}: {
  item: InfoItem
  ratio: number
  search: string
  onMeasure: (id: string, ratio: number) => void
}) {
  const [failed, setFailed] = useState(false)
  const cover = item.cover
  const image = cover && !failed ? cover : null
  const excerpt = item.excerpt || item.text || ''
  const time = relativeTime(item.published_at || item.collected_at)
  const isVideo = item.kind === 'video' || item.kind === 'mixed'
  const duration = formatDuration(cover?.duration_ms)

  function handleLoad(event: SyntheticEvent<HTMLImageElement>) {
    const node = event.currentTarget
    if (node.naturalWidth > 0 && node.naturalHeight > 0) {
      onMeasure(item.id, node.naturalWidth / node.naturalHeight)
    }
  }

  return (
    <article className="group overflow-hidden rounded-xl border border-line bg-panel transition [content-visibility:auto] hover:border-signal/30 lg:hover:-translate-y-0.5">
      <Link to={`/share/info/${item.id}${search}`} className="block">
        <div className="relative w-full overflow-hidden bg-ink" style={image ? { aspectRatio: `${ratio}` } : undefined}>
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
            <InfoTextCover seed={item.cover_seed} text={excerpt} channel="" time={time} />
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

          {item.is_featured || item.ai_score != null ? (
            <span className="pointer-events-none absolute left-1.5 top-1.5 flex items-center gap-1 rounded bg-black/55 px-1.5 py-0.5 text-[10px] font-medium text-paper">
              {item.is_featured ? <Star size={11} className="fill-amber-300 text-amber-300" /> : null}
              {item.ai_score != null ? <span>{item.ai_score}</span> : null}
            </span>
          ) : null}
        </div>

        {image && excerpt ? (
          <p className="line-clamp-2 px-3 pt-2 text-[13px] leading-5 text-paper">{excerpt}</p>
        ) : null}
        {!image && excerpt ? <span className="sr-only">{excerpt}</span> : null}
      </Link>

      <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 text-[11px] text-mist">
        <span>{infoKindLabel(item.kind)}</span>
        {time ? <span>· {time}</span> : null}
        {item.ai_tags.slice(0, 2).map((tag) => (
          <span key={tag} className="rounded-full border border-line px-1.5 py-0.5">
            #{tag}
          </span>
        ))}
      </div>
    </article>
  )
}

/** 公开详情：覆盖在列表之上的子路由，展示媒体与正文，不含渠道来源。 */
export function PublicInfoItemPage() {
  const { itemId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()

  const query = useQuery({
    queryKey: ['public-info-item', itemId],
    queryFn: () => api.publicInfoItem(itemId),
    enabled: Boolean(itemId),
  })
  const item = query.data
  const media = useMemo(() => (item?.media ?? []).filter((entry) => entry.kind !== 'poster'), [item])
  const locked = query.error instanceof ApiError && query.error.status === 401

  const [index, setIndex] = useState(0)
  const [broken, setBroken] = useState<Record<string, boolean>>({})
  const safeIndex = media.length ? Math.min(index, media.length - 1) : 0
  const current = media[safeIndex] ?? null

  const close = useCallback(() => {
    if (location.key && location.key !== 'default') navigate(-1)
    else navigate('/share/info', { replace: true })
  }, [location.key, navigate])

  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink">
      <header className="sticky top-0 z-10 flex items-center border-b border-line bg-ink/95 px-4 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] backdrop-blur">
        <button
          type="button"
          onClick={close}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-mist transition hover:bg-white/5 hover:text-paper"
        >
          <ArrowLeft size={17} /> 返回
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {query.isLoading ? (
          <div className="py-16 text-center text-sm text-mist">加载中…</div>
        ) : null}

        {locked ? (
          <div className="flex min-h-full items-center justify-center px-4 py-16">
            <PublicInfoGate onUnlocked={() => void query.refetch()} />
          </div>
        ) : null}

        {!query.isLoading && !locked && (!item || query.isError) ? (
          <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
            <ImageOff size={22} className="text-mist" />
            <div className="text-sm text-danger">{errorMessage(query.error, '内容不存在或已下架')}</div>
            <Button type="button" variant="line" onClick={close}>
              返回列表
            </Button>
          </div>
        ) : null}

        {item ? (
          <div className="mx-auto w-full max-w-[760px] space-y-4 px-4 py-4 pb-[max(2rem,env(safe-area-inset-bottom))]">
            {item.ai_score != null ||
            item.is_featured ||
            item.ai_reason ||
            item.ai_label ||
            item.ai_tags.length ? (
              <div>
                <div className="rounded-xl border border-line bg-panel-2 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="inline-flex items-center gap-1 text-paper">
                      <Sparkles size={13} className="text-signal" /> AI 分析
                    </span>
                    {item.is_featured ? <Badge tone="ok">精选</Badge> : null}
                    {item.ai_score != null ? <Badge tone="info">{item.ai_score} 分</Badge> : null}
                    {item.ai_label ? (
                      <Badge tone={item.ai_label === 'ad' ? 'bad' : 'mist'}>{aiLabelText(item.ai_label)}</Badge>
                    ) : null}
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
                </div>
              </div>
            ) : null}
            {!item.content_html && current ? (
              <div className="overflow-hidden rounded-xl border border-line">
                <div
                  className="relative mx-auto flex max-h-[72vh] w-full items-center justify-center"
                  style={{ aspectRatio: `${infoMediaRatio(current, item)}` }}
                >
                  {renderMedia(current, broken[current.id] ?? false, () =>
                    setBroken((prev) => ({ ...prev, [current.id]: true })),
                  )}
                  {media.length > 1 ? (
                    <>
                      <button
                        type="button"
                        aria-label="上一项"
                        onClick={() => setIndex((value) => (value - 1 + media.length) % media.length)}
                        className="absolute left-2 top-1/2 inline-flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/50 text-paper transition hover:bg-black/70"
                      >
                        <ChevronLeft size={18} />
                      </button>
                      <button
                        type="button"
                        aria-label="下一项"
                        onClick={() => setIndex((value) => (value + 1) % media.length)}
                        className="absolute right-2 top-1/2 inline-flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/50 text-paper transition hover:bg-black/70"
                      >
                        <ChevronRight size={18} />
                      </button>
                      <span className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] text-paper">
                        {safeIndex + 1}/{media.length}
                      </span>
                    </>
                  ) : null}
                </div>
                {media.length > 1 ? (
                  <div className="flex flex-wrap items-center justify-center gap-1.5 border-t border-line px-3 py-2.5">
                    {media.map((entry, position) => (
                      <button
                        key={entry.id}
                        type="button"
                        aria-label={`第 ${position + 1} 项`}
                        aria-current={position === safeIndex}
                        onClick={() => setIndex(position)}
                        className="inline-flex h-6 items-center px-0.5"
                      >
                        <span
                          className={cn(
                            'h-1.5 rounded-full transition-all',
                            position === safeIndex ? 'w-5 bg-signal' : 'w-1.5 bg-white/25',
                          )}
                        />
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-mist">
                <Badge tone="mist">{infoKindLabel(item.kind)}</Badge>
                <span>{formatTime(item.published_at || item.collected_at)}</span>
              </div>

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
            </div>
          </div>
        ) : null}
      </div>
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

function renderMedia(media: InfoMedia, broken: boolean, onBroken: () => void) {  if (broken) {
    return (
      <div className="flex flex-col items-center gap-2 text-mist">
        <ImageOff size={20} />
        <span className="text-xs">媒体不可用</span>
      </div>
    )
  }
  if (media.kind === 'video' && media.url) {
    return (
      <video
        src={media.url}
        poster={media.poster_url || undefined}
        controls
        playsInline
        className="max-h-full max-w-full"
        onError={onBroken}
      >
        <track kind="captions" />
      </video>
    )
  }
  if (!media.url) {
    return <span className="text-xs text-mist">媒体不可用</span>
  }
  return (
    <img
      src={media.url}
      alt=""
      referrerPolicy="no-referrer"
      onError={onBroken}
      className="max-h-full max-w-full object-contain"
    />
  )
}

export default PublicInfoPage
