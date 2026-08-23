import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CircleDot, RefreshCw, Route, Server } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

export function AgentDetailPage() {
  const { agentId = '' } = useParams()
  const queryClient = useQueryClient()
  const { data: agent, isLoading } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => api.agent(agentId),
    enabled: Boolean(agentId),
    refetchInterval: 10_000,
  })
  const refreshModels = useMutation({
    mutationFn: (routeId: string) => api.refreshAgentRouteModels(agentId, routeId),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
      await queryClient.invalidateQueries({ queryKey: ['agents'] })
      notifyOk(`已同步 ${result.models.length} 个模型`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '刷新模型失败')),
  })

  if (isLoading) return <div className="py-12 text-sm text-mist">正在加载网关代理详情...</div>
  if (!agent) return <div className="py-12 text-sm text-mist">未找到该网关代理。</div>

  return (
    <div className="space-y-5">
      <Link to="/agents" className="inline-flex items-center gap-1.5 text-sm text-mist hover:text-paper">
        <ArrowLeft size={16} /> 返回网关代理
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <Server size={24} className="shrink-0 text-info" />
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-semibold">{agent.agent_id}</h1>
            <p className="mt-1 text-sm text-mist">最后连接：{formatTime(agent.last_connected_at)}</p>
          </div>
        </div>
        <Badge tone={agent.status === 'online' ? 'ok' : 'mist'}><CircleDot size={12} /> {agent.status === 'online' ? '在线' : '离线'}</Badge>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card><div className="text-xs uppercase tracking-[0.16em] text-mist">已注册路由</div><div className="mt-3 font-mono text-3xl text-info">{agent.routes.length}</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.16em] text-mist">已缓存模型</div><div className="mt-3 font-mono text-3xl text-signal">{agent.routes.reduce((total, route) => total + route.models.length, 0)}</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.16em] text-mist">最后断开</div><div className="mt-3 text-sm text-paper">{formatTime(agent.last_disconnected_at)}</div></Card>
      </div>

      <section className="overflow-hidden rounded-lg border border-line">
        <div className="border-b border-line bg-panel-2 px-4 py-3 text-xs uppercase tracking-[0.16em] text-mist">路由与模型</div>
        <div className="divide-y divide-line">
          {agent.routes.map((route) => {
            const pending = refreshModels.isPending && refreshModels.variables === route.id
            return (
              <div key={route.id} className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(220px,0.8fr)_minmax(0,1fr)_auto] lg:items-start">
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><Route size={16} className="text-info" /><span className="font-medium text-paper">{route.name}</span></div>
                  <code className="mt-2 block truncate text-xs text-mist">{route.id}</code>
                  <Badge tone="info" title="上游协议提供商"><span>{route.provider}</span></Badge>
                </div>
                <div>
                  <div className="flex flex-wrap gap-1.5">
                    {route.models.map((model) => <Badge key={model} tone="mist">{model}</Badge>)}
                    {!route.models.length ? <span className="text-sm text-mist">尚未从上游同步模型</span> : null}
                  </div>
                  <div className="mt-2 text-xs text-mist">上次同步：{formatTime(route.models_updated_at)}</div>
                </div>
                <Button variant="line" disabled={agent.status !== 'online' || pending} onClick={() => refreshModels.mutate(route.id)} title={agent.status === 'online' ? '从上游刷新模型' : '网关代理离线时无法刷新模型'}>
                  <RefreshCw size={16} className={pending ? 'animate-spin' : ''} /> 刷新模型
                </Button>
              </div>
            )
          })}
          {!agent.routes.length ? <div className="px-4 py-10 text-center text-sm text-mist">该网关代理尚未注册路由。</div> : null}
        </div>
      </section>
    </div>
  )
}