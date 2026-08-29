import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui'
import { api, type DashboardBenchmarkTop, type DashboardLeaderboardTop } from '../lib/api'
import { cn, formatContextWindow, formatTokenCount } from '../lib/utils'

type Metric = {
  label: string
  value: string | number
  to: string
  warn?: boolean
  span?: boolean
}

function MetricCard({ label, value, to, warn, span }: Metric) {
  return (
    <Link to={to} className={cn('block min-h-11 min-w-0', span && 'col-span-2 lg:col-span-1')}>
      <Card className="h-full hover:border-signal/40">
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

function LeaderboardTopSection({ items }: { items: DashboardLeaderboardTop[] }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs uppercase tracking-[0.16em] text-mist">排行榜前三</h2>
        <Link to="/leaderboard" className="text-xs text-signal hover:underline">
          查看全部
        </Link>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {items.length
          ? items.map((item, index) => (
              <Link key={`${item.slug || item.name}-${index}`} to="/leaderboard" className="block min-h-11 min-w-0">
                <Card className="h-full hover:border-signal/40">
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
                        上下文 {formatContextWindow(item.context_window_tokens)}
                        {item.max_output_tokens ? ` · 输出 ${formatContextWindow(item.max_output_tokens)}` : ''}
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
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs uppercase tracking-[0.16em] text-mist">测速速度前三</h2>
        <Link to="/benchmark/history" className="text-xs text-signal hover:underline">
          查看历史
        </Link>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {items.length
          ? items.map((item, index) => (
              <Link
                key={`${item.run_id ?? 'x'}-${item.model}-${item.account_name}-${index}`}
                to="/benchmark/history"
                className="block min-h-11 min-w-0"
              >
                <Card className="h-full hover:border-signal/40">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">#{index + 1}</div>
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
  const today: Metric[] = [
    { label: '今日请求', value: data?.today_requests ?? '—', to: '/logs' },
    { label: '今日失败', value: data?.today_failures ?? '—', to: '/logs', warn: true },
    { label: '今日 Token', value: formatTokenCount(data?.today_tokens), to: '/logs', span: true },
  ]
  const totals: Metric[] = [
    { label: '总请求', value: data?.total_requests ?? '—', to: '/logs' },
    { label: '总 Token', value: formatTokenCount(data?.total_tokens), to: '/logs' },
  ]
  const resources: Metric[] = [
    { label: '上游账号', value: data?.account_count ?? '—', to: '/accounts' },
    { label: '异常账号', value: data?.unhealthy_count ?? '—', to: '/accounts', warn: true },
    { label: 'API Key', value: data?.key_count ?? '—', to: '/keys' },
    {
      label: '网关在线',
      value: data ? `${data.agent_online_count}/${data.agent_count}` : '—',
      to: '/agents',
    },
    { label: '工具', value: data?.tool_count ?? '—', to: '/tools' },
    { label: 'Skills', value: data?.skill_count ?? '—', to: '/skills' },
    { label: '测速条数', value: data?.benchmark_count ?? '—', to: '/benchmark/history' },
  ]

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">概览</h1>
        <p className="mt-1 text-sm text-mist">当前运行切片，点卡片进入对应列表。</p>
      </div>
      <MetricSection title="今日" items={today} className="grid grid-cols-2 gap-3 lg:grid-cols-3" />
      <MetricSection title="累计" items={totals} className="grid grid-cols-2 gap-3 lg:max-w-xl" />
      <MetricSection title="资源" items={resources} className="grid grid-cols-2 gap-3 lg:grid-cols-4" />
      <LeaderboardTopSection items={data?.leaderboard_top ?? []} />
      <BenchmarkSpeedTopSection items={data?.benchmark_speed_top ?? []} />
    </div>
  )
}
