import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui'
import { api } from '../lib/api'
import { cn, formatTokenCount } from '../lib/utils'

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
    </div>
  )
}
