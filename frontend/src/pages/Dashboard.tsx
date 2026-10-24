import { Suspense, lazy, useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui'
import { api, type DashboardBenchmarkTop, type DashboardLeaderboardStatus, type DashboardLeaderboardTop } from '../lib/api'
import { useSession } from '../lib/session'
import { cn, formatContextWindow, formatTime, formatTokenCount } from '../lib/utils'
import { PageHeader } from '../components/layout-primitives'
import { DashboardSummary } from '../components/dashboard-summary'
import { ChartSkeleton } from '../components/charts/chart-card'

// 图表区按需加载：echarts 体积不小，别让其它后台路由（和首屏之外的模块）一起买单
const DashboardCharts = lazy(() =>
  import('../components/dashboard-charts').then((module) => ({ default: module.DashboardCharts })),
)

/** 统计区间：默认一周，可切到 14/30/90 天看长一点的趋势 */
const RANGE_OPTIONS = [7, 14, 30, 90] as const
type RangeDays = (typeof RANGE_OPTIONS)[number]
const RANGE_STORAGE_KEY = 'pivot:dashboard-days'

function readStoredRange(): RangeDays {
  const stored = Number(localStorage.getItem(RANGE_STORAGE_KEY))
  return (RANGE_OPTIONS as readonly number[]).includes(stored) ? (stored as RangeDays) : 7
}

function RangeTabs({ days, onChange }: { days: RangeDays; onChange: (days: RangeDays) => void }) {
  return (
    <div role="group" aria-label="统计区间" className="inline-flex rounded-lg border border-line bg-panel-2 p-0.5">
      {RANGE_OPTIONS.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={days === option}
          onClick={() => onChange(option)}
          className={cn(
            'min-h-9 rounded-md px-2.5 text-xs transition-colors',
            days === option ? 'bg-signal/15 text-signal' : 'text-mist-soft hover:text-paper',
          )}
        >
          {option} 天
        </button>
      ))}
    </div>
  )
}

function ChartsSkeleton() {
  return (
    <div className="grid gap-3 xl:grid-cols-2" aria-hidden="true">
      <ChartSkeleton />
      <ChartSkeleton />
      <ChartSkeleton />
      <ChartSkeleton />
    </div>
  )
}

type Metric = {
  label: string
  value: string | number
  to: string
  perm?: string
  warn?: boolean
  span?: boolean
}

function MetricCard({ label, value, to, perm, warn, span }: Metric) {
  const { can } = useSession()
  const body = (
    <Card className={cn('h-full', can(perm) && 'hover:border-signal/40')}>
      <div className="text-[10px] uppercase tracking-[0.16em] text-mist lg:text-xs">{label}</div>
      <div
        className={cn(
          'mt-2 truncate font-mono text-xl tabular-nums lg:mt-3 lg:text-3xl',
          warn ? 'text-warn' : 'text-signal',
        )}
        title={String(value)}
      >
        {value}
      </div>
    </Card>
  )
  if (!can(perm)) {
    return <div className={cn('min-h-11 min-w-0', span && 'col-span-2 lg:col-span-1')}>{body}</div>
  }
  return (
    <Link to={to} className={cn('block min-h-11 min-w-0', span && 'col-span-2 lg:col-span-1')}>
      {body}
    </Link>
  )
}

function MetricSection({
  title,
  items,
  className,
}: {
  title: string
  items: Metric[]
  className: string
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-xs uppercase tracking-[0.16em] text-mist">{title}</h2>
      <div className={className}>
        {items.map((item) => (
          <MetricCard key={item.label} {...item} />
        ))}
      </div>
    </section>
  )
}

function formatScore(value: number | null | undefined) {
  if (value == null) return '—'
  return value.toFixed(1)
}

function formatSpeed(value: number | null | undefined) {
  if (value == null) return '—'
  const rounded = Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, '')
  return `${rounded} tok/s`
}

function rankLabel(rank: number | null | undefined, index: number) {
  return rank ?? index + 1
}

function LeaderboardStatusBadge({ status }: { status?: DashboardLeaderboardStatus }) {
  const ok = status?.ok ?? false
  const label = status ? (ok ? '正常' : '同步失败') : '未知'
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 text-xs', ok ? 'text-ok' : 'text-danger')}
      title={status?.error_message || undefined}
    >
      <span className={cn('h-2 w-2 rounded-full', ok ? 'bg-ok' : 'bg-danger')} />
      {label}
    </span>
  )
}

function LeaderboardTopSection({
  items,
  status,
}: {
  items: DashboardLeaderboardTop[]
  status?: DashboardLeaderboardStatus
}) {
  const { can } = useSession()
  const canView = can('leaderboard:view')
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-xs uppercase tracking-[0.16em] text-mist">排行榜前三</h2>
          <LeaderboardStatusBadge status={status} />
        </div>
        {canView ? (
          <Link to="/leaderboard" className="text-xs text-signal hover:underline">
            查看全部
          </Link>
        ) : null}
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {items.length
          ? items.map((item, index) => (
              <Link
                key={`${item.slug || item.name}-${index}`}
                to={canView ? '/leaderboard' : '#'}
                className="block min-h-11 min-w-0"
                onClick={(event) => {
                  if (!canView) event.preventDefault()
                }}
              >
                <Card className={cn('h-full', canView && 'hover:border-signal/40')}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">#{rankLabel(item.rank, index)}</div>
                      <div className="mt-2 truncate font-medium" title={item.name}>
                        {item.name}
                      </div>
                      <div className="mt-1 truncate text-xs text-mist" title={item.provider || undefined}>
                        {item.provider || '—'}
                      </div>
                      <div className="mt-2 truncate font-mono text-[11px] tabular-nums text-mist">
                        上下文 {formatContextWindow(item.context_window_tokens)} · 输出 {formatContextWindow(item.max_output_tokens)}
                      </div>
                    </div>
                    <div className="shrink-0 font-mono text-xl tabular-nums text-signal">{formatScore(item.score)}</div>
                  </div>
                </Card>
              </Link>
            ))
          : (
              <Card className="col-span-full text-sm text-mist">暂无榜单缓存，去模型榜页拉取后即可展示。</Card>
            )}
      </div>
    </section>
  )
}

function BenchmarkSpeedTopSection({ items }: { items: DashboardBenchmarkTop[] }) {
  const { can } = useSession()
  const canView = can('benchmark_history:view')
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs uppercase tracking-[0.16em] text-mist">近期测试</h2>
        {canView ? (
          <Link to="/benchmark/history" className="text-xs text-signal hover:underline">
            查看历史
          </Link>
        ) : null}
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {items.length
          ? items.map((item, index) => (
              <Link
                key={`${item.run_id ?? 'x'}-${item.model}-${item.account_name}-${index}`}
                to={canView ? '/benchmark/history' : '#'}
                className="block min-h-11 min-w-0"
                onClick={(event) => {
                  if (!canView) event.preventDefault()
                }}
              >
                <Card className={cn('h-full', canView && 'hover:border-signal/40')}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">{formatTime(item.created_at)}</div>
                      <div className="mt-2 truncate font-medium" title={item.model}>
                        {item.model}
                      </div>
                      <div className="mt-1 truncate text-xs text-mist" title={`${item.account_name} · ${item.provider}`}>
                        {item.account_name} · {item.provider || '—'}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="font-mono text-lg tabular-nums text-signal">{formatSpeed(item.output_tokens_per_second)}</div>
                      <div className="mt-1 font-mono text-[10px] tabular-nums text-mist">
                        首 token {item.first_token_ms ?? '—'} ms
                      </div>
                    </div>
                  </div>
                </Card>
              </Link>
            ))
          : (
              <Card className="col-span-full text-sm text-mist">暂无成功测速记录。</Card>
            )}
      </div>
    </section>
  )
}

export function DashboardPage() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  const [days, setDays] = useState<RangeDays>(readStoredRange)
  useEffect(() => {
    localStorage.setItem(RANGE_STORAGE_KEY, String(days))
  }, [days])
  const { data: stats } = useQuery({
    queryKey: ['dashboard-stats', days],
    queryFn: () => api.dashboardStats(days),
    // 切区间时先保留上一份数据，避免图表整块闪成骨架
    placeholderData: keepPreviousData,
  })
  const today: Metric[] = [
    { label: '今日调用', value: data?.today_requests ?? '—', to: '/logs', perm: 'logs:view' },
    { label: '今日失败', value: data?.today_failures ?? '—', to: '/logs', perm: 'logs:view', warn: true },
    { label: '今日 Token', value: formatTokenCount(data?.today_tokens), to: '/logs', perm: 'logs:view', span: true },
  ]
  const totals: Metric[] = [
    { label: '总调用', value: data?.total_requests ?? '—', to: '/logs', perm: 'logs:view' },
    { label: '总 Token', value: formatTokenCount(data?.total_tokens), to: '/logs', perm: 'logs:view' },
  ]
  const resources: Metric[] = [
    { label: '上游账号', value: data?.account_count ?? '—', to: '/accounts', perm: 'accounts:view' },
    { label: '异常账号', value: data?.unhealthy_count ?? '—', to: '/accounts', perm: 'accounts:view', warn: true },
    { label: 'API Key', value: data?.key_count ?? '—', to: '/keys', perm: 'keys:view' },
    {
      label: '网关在线',
      value: data ? `${data.agent_online_count}/${data.agent_count}` : '—',
      to: '/agents',
      perm: 'agents:view',
    },
    { label: '工具', value: data?.tool_count ?? '—', to: '/tools', perm: 'tools:view' },
    { label: 'Skills', value: data?.skill_count ?? '—', to: '/skills', perm: 'skills:view' },
    { label: '测速条数', value: data?.benchmark_count ?? '—', to: '/benchmark/history', perm: 'benchmark_history:view' },
  ]

  return (
    <div className="space-y-5">
      <PageHeader
        title="概览"
        description="调用趋势、消耗结构与资源快照，点卡片进入对应列表。图表聚合按缓存更新（默认 30 分钟），卡片数字实时。"
        actions={<RangeTabs days={days} onChange={setDays} />}
      />
      <DashboardSummary data={stats} />
      {stats ? (
        <Suspense fallback={<ChartsSkeleton />}>
          <DashboardCharts data={stats} onSelectDays={(value) => setDays(value as RangeDays)} />
        </Suspense>
      ) : (
        <ChartsSkeleton />
      )}
      <MetricSection title="今日" items={today} className="grid grid-cols-2 gap-3 lg:grid-cols-3" />
      <MetricSection title="累计" items={totals} className="grid grid-cols-2 gap-3 lg:max-w-xl" />
      <MetricSection title="资源" items={resources} className="grid grid-cols-2 gap-3 lg:grid-cols-4" />
      <LeaderboardTopSection items={data?.leaderboard_top ?? []} status={data?.leaderboard_status} />
      <BenchmarkSpeedTopSection items={data?.benchmark_speed_top ?? []} />
    </div>
  )
}
