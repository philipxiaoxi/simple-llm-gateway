import { useQuery } from '@tanstack/react-query'
import { ChevronRight, CircleDot, RefreshCw, Server } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'

export function AgentsPage() {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['agents'],
    queryFn: api.agents,
    refetchInterval: 10_000,
  })
  const agents = data?.items ?? []
  const onlineCount = agents.filter((agent) => agent.status === 'online').length

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">网关代理</h1>
          <p className="mt-1 text-sm text-mist">已发现的机器与其连接状态、路由和上游模型缓存。</p>
        </div>
        <Button variant="line" onClick={() => void refetch()} disabled={isFetching} title="刷新在线状态">
          <RefreshCw size={16} className={isFetching ? 'animate-spin' : ''} />
          刷新
        </Button>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">在线机器</div>
          <div className="mt-3 font-mono text-3xl text-signal">{data ? onlineCount : '—'}</div>
          <div className="mt-1 text-xs text-mist">共 {data?.total ?? 0} 台已发现机器</div>
        </Card>
      </div>

      <div className="mt-6 overflow-hidden rounded-lg border border-line">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 border-b border-line bg-panel-2 px-4 py-3 text-xs uppercase tracking-[0.16em] text-mist sm:grid-cols-[minmax(0,1fr)_minmax(220px,1fr)_auto_auto]">
          <span>机器</span>
          <span className="hidden sm:block">已注册路由</span>
          <span>状态</span>
        </div>
        {agents.map((agent) => (
          <Link key={agent.agent_id} to={`/agents/${encodeURIComponent(agent.agent_id)}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 border-b border-line px-4 py-4 transition hover:bg-white/[0.03] last:border-b-0 sm:grid-cols-[minmax(0,1fr)_minmax(220px,1fr)_auto_auto]">
            <div className="flex min-w-0 items-center gap-3">
              <Server size={18} className="shrink-0 text-info" />
              <code className="truncate text-sm text-paper">{agent.agent_id}</code>
            </div>
            <div className="hidden flex-wrap gap-1.5 sm:flex">
              {agent.routes.map((route) => <Badge key={route.id} tone="info">{route.name} · {route.models.length} 模型</Badge>)}
            </div>
            <Badge tone={agent.status === 'online' ? 'ok' : 'mist'}><CircleDot size={12} /> {agent.status === 'online' ? '在线' : '离线'}</Badge>
            <ChevronRight size={18} className="hidden text-mist sm:block" aria-hidden="true" />
          </Link>
        ))}
        {!agents.length && !isFetching ? (
          <div className="px-4 py-12 text-center text-sm text-mist">当前还没有发现过网关代理。</div>
        ) : null}
      </div>
    </div>
  )
}