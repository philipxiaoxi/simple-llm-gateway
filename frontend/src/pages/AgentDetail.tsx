import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, CircleDot, RefreshCw, RotateCcw, Route, Server } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Switch } from '../components/ui'
import { api, type ModelCaps } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime, modelCapsHint } from '../lib/utils'

function RoutePrefixEditor({
  accountId,
  initialPrefix,
  onSaved,
}: {
  accountId: number
  initialPrefix: string
  onSaved: () => void
}) {
  const [prefix, setPrefix] = useState(initialPrefix)
  const [pending, setPending] = useState(false)
  const [open, setOpen] = useState(false)
  const normalizedPrefix = prefix.trim()
  const isValid = normalizedPrefix.length === 0 || /^[A-Za-z0-9_-]{1,32}$/.test(normalizedPrefix)
  const hasChanges = normalizedPrefix !== initialPrefix

  useEffect(() => {
    setPrefix(initialPrefix)
  }, [initialPrefix])

  async function save() {
    setPending(true)
    try {
      await api.updateAccount(accountId, { model_prefix: prefix.trim() || undefined })
      notifyOk('已保存模型前缀')
      onSaved()
    } catch (caught) {
      notifyBad(errorMessage(caught, '保存前缀失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <>
      <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-line bg-ink/40 px-3 py-2">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.12em] text-mist">模型前缀</div>
          <div className="mt-1 truncate font-mono text-xs text-paper">{initialPrefix || '自动生成'}</div>
        </div>
        <Button type="button" variant="line" className="shrink-0" onClick={() => setOpen(true)}>
          <Route size={15} /> 编辑
        </Button>
      </div>
      {open ? (
        <Dialog title="编辑模型前缀" onClose={() => setOpen(false)}>
          <div className="grid gap-4">
            <div className="border-l-2 border-info pl-3 text-sm leading-5 text-mist">
              前缀用于区分同名模型。保存后，冲突模型会以 <span className="font-mono text-paper">prefix/model</span> 的形式对外提供。
            </div>
            <Field label="模型前缀">
              <div className="relative">
                <Input
                  autoFocus
                  className={`pr-9 font-mono ${!isValid ? 'border-danger focus:border-danger' : ''}`}
                  value={prefix}
                  onChange={(event) => setPrefix(event.target.value)}
                  placeholder="自动生成"
                  aria-invalid={!isValid}
                />
                {isValid && normalizedPrefix ? <Check size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-signal" /> : null}
              </div>
              <div className="mt-1.5 flex items-start justify-between gap-3 text-[11px] leading-4 text-mist">
                <span>{isValid ? '支持字母、数字、下划线和短横线。' : '格式无效：仅支持字母、数字、下划线和短横线，最长 32 位。'}</span>
                <span className="shrink-0 font-mono">{normalizedPrefix.length}/32</span>
              </div>
            </Field>
            <div className="flex min-w-0 items-center gap-2 rounded border border-line bg-panel px-3 py-2 text-xs">
              <span className="shrink-0 text-mist">公开名称</span>
              <span className="truncate font-mono text-paper">{normalizedPrefix || '自动生成'}/model-name</span>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
              <Button type="button" variant="ghost" disabled={pending || !normalizedPrefix} onClick={() => setPrefix('')}>
                <RotateCcw size={15} /> 恢复自动生成
              </Button>
              <div className="flex gap-2">
                <Button type="button" variant="ghost" disabled={pending} onClick={() => setOpen(false)}>取消</Button>
                <Button type="button" variant="line" disabled={pending || !isValid || !hasChanges} onClick={() => void save()}>
                  {pending ? <RefreshCw size={15} className="animate-spin" /> : <Check size={15} />} 保存更改
                </Button>
              </div>
            </div>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}

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
  const toggleModelEnabled = useMutation({
    mutationFn: ({ accountId, model }: { accountId: number; model: ModelCaps }) =>
      api.updateAccountModel(accountId, model.id, { enabled: model.enabled === false }),
    onSuccess: async (_result, variables) => {
      await queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
      await queryClient.invalidateQueries({ queryKey: ['agents'] })
      await queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
      await queryClient.invalidateQueries({ queryKey: ['benchmark-accounts'] })
      notifyOk(variables.model.enabled === false ? `已启用 ${variables.model.id}` : `已关闭 ${variables.model.id}`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新模型状态失败')),
  })
  const updateRouteStatus = useMutation({
    mutationFn: ({ accountId, status }: { accountId: number; status: string }) =>
      api.updateAccount(accountId, { status }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
      await queryClient.invalidateQueries({ queryKey: ['agents'] })
      await queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
      await queryClient.invalidateQueries({ queryKey: ['keys'] })
      notifyOk('路由状态已更新')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新路由状态失败')),
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
            const statusPending = updateRouteStatus.isPending && updateRouteStatus.variables?.accountId === route.account_id
            return (
              <div key={route.id} className="grid gap-4 px-4 py-4 lg:grid-cols-[minmax(220px,0.8fr)_minmax(0,1fr)] lg:items-start">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2"><Route size={16} className="text-info" /><span className="font-medium text-paper">{route.name}</span><Badge tone={route.status === 'active' ? 'ok' : 'mist'}>{route.status === 'active' ? '启用' : '停用'}</Badge></div>
                  <code className="mt-2 block truncate text-xs text-mist">{route.id}</code>
                  <Badge tone="info" title="上游协议提供商"><span>{route.provider}</span></Badge>
                  {route.account_id ? (
                    <RoutePrefixEditor
                      accountId={route.account_id}
                      initialPrefix={route.model_prefix ?? ''}
                      onSaved={() => {
                        void queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
                        void queryClient.invalidateQueries({ queryKey: ['agents'] })
                        void queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
                      }}
                    />
                  ) : null}
                  <div className="mt-3 flex flex-wrap items-center justify-start gap-2">
                    {route.account_id ? (
                      <Button
                        variant="line"
                        disabled={statusPending}
                        onClick={() => updateRouteStatus.mutate({ accountId: route.account_id as number, status: route.status === 'active' ? 'disabled' : 'active' })}
                      >
                        {route.status === 'active' ? '停用路由' : '启用路由'}
                      </Button>
                    ) : null}
                    <Button variant="line" disabled={agent.status !== 'online' || route.status !== 'active' || pending} onClick={() => refreshModels.mutate(route.id)} title={agent.status === 'online' && route.status === 'active' ? '从上游刷新模型' : '网关代理或路由停用时无法刷新模型'}>
                      <RefreshCw size={16} className={pending ? 'animate-spin' : ''} /> 刷新模型
                    </Button>
                  </div>
                </div>
                <div>
                  <div className="flex flex-col gap-1.5">
                    {route.models.map((model) => {
                      const enabled = model.enabled !== false
                      const pending =
                        toggleModelEnabled.isPending
                        && toggleModelEnabled.variables?.accountId === route.account_id
                        && toggleModelEnabled.variables?.model.id === model.id
                      return (
                        <div
                          key={model.id}
                          className={cn(
                            'flex min-w-0 items-center gap-2 rounded-md border px-2.5 py-1.5',
                            enabled ? 'border-line bg-ink/40' : 'border-dashed border-line/80 bg-ink/20',
                          )}
                        >
                          <span className="min-w-0 flex-1">
                            <span
                              className={cn('block truncate font-mono text-xs', enabled ? 'text-paper' : 'text-mist')}
                              title={model.id}
                            >
                              {model.id}
                            </span>
                            <span className="mt-0.5 block text-[11px] text-mist">{modelCapsHint(model) || (enabled ? '对外可见' : '已关闭，测速仍可用')}</span>
                          </span>
                          {route.account_id ? (
                            <Switch
                              checked={enabled}
                              disabled={pending}
                              onCheckedChange={() => toggleModelEnabled.mutate({ accountId: route.account_id as number, model })}
                            />
                          ) : null}
                        </div>
                      )
                    })}
                    {!route.models.length ? <span className="text-sm text-mist">尚未从上游同步模型</span> : null}
                  </div>
                  <div className="mt-2 text-xs text-mist">上次同步：{formatTime(route.models_updated_at)}</div>
                </div>
              </div>
            )
          })}
          {!agent.routes.length ? <div className="px-4 py-10 text-center text-sm text-mist">该网关代理尚未注册路由。</div> : null}
        </div>
      </section>
    </div>
  )
}