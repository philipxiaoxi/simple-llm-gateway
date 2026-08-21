import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui'
import { api } from '../lib/api'

export function DashboardPage() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  const cards = [
    { label: '上游账号', value: data?.account_count ?? '—', to: '/accounts' },
    { label: '异常账号', value: data?.unhealthy_count ?? '—', to: '/accounts', warn: true },
    { label: '今日请求', value: data?.today_requests ?? '—', to: '/logs' },
    { label: '今日失败', value: data?.today_failures ?? '—', to: '/logs', warn: true },
    { label: '今日 Token', value: data?.today_tokens ?? '—', to: '/logs' },
    { label: '总请求', value: data?.total_requests ?? '—', to: '/logs' },
    { label: '总 Token', value: data?.total_tokens ?? '—', to: '/logs' },
  ]

  return (
    <div>
      <h1 className="text-2xl font-semibold">概览</h1>
      <p className="mt-1 text-sm text-mist">当前中转台的运行切片，点卡片进入对应列表。</p>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <Link key={card.label} to={card.to}>
            <Card className="hover:border-signal/40">
              <div className="text-xs uppercase tracking-[0.16em] text-mist">{card.label}</div>
              <div className={`mt-3 font-mono text-3xl ${card.warn ? 'text-warn' : 'text-signal'}`}>{card.value}</div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  )
}
