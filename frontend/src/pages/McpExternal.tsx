import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Download, KeyRound, Pencil, Plus, RefreshCw, Server, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { EmptyState, ListSkeleton } from '../components/data-view'
import { ExternalMcpHeartbeat } from '../components/ExternalMcpHeartbeat'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, webHeaders, type ExternalMcpCredential, type ExternalMcpService } from '../lib/api'
import { useCan } from '../lib/session'
import { cn, copyText, errorMessage } from '../lib/utils'

const serviceKey = ['mcp-external']
const statusText = { active: '有效', expired: '已过期', revoked: '已销毁' }
const date = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
const expiry = (value: string | null) => value ? date(value) : '永不过期'
const serviceStatus = (service: ExternalMcpService) => service.online ? '在线' : service.version ? '离线' : '未注册'

function DialogActions({ close, children }: { close: () => void; children: ReactNode }) {
  return <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={close}>取消</Button>{children}</div>
}

function DurationSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <Field label="有效期" htmlFor="external-mcp-expiry">
    <Select id="external-mcp-expiry" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="never">永不过期</option>
      {[7, 30, 90].map((days) => <option key={days} value={days}>{days} 天</option>)}
    </Select>
  </Field>
}

function ServiceNav({ services, service, onSelect }: {
  services: ExternalMcpService[]; service: ExternalMcpService; onSelect: (id: string) => void
}) {
  return <Card className="self-start space-y-2 p-3">
    <h3 className="px-2 text-xs font-medium leading-5 text-mist">服务列表 <span className="ml-1 tabular-nums">{services.length}</span></h3>
    <div className="lg:hidden">
      <Select aria-label="选择服务" value={service.id} onChange={(event) => onSelect(event.target.value)}>
        {services.map((item) => <option key={item.id} value={item.id}>{item.name} · {serviceStatus(item)}</option>)}
      </Select>
    </div>
    <nav aria-label="外部服务列表" className="hidden space-y-1 lg:block">
      {services.map((item) => <Button key={item.id} type="button" variant="ghost" size="sm"
        aria-current={item.id === service.id ? 'true' : undefined}
        className={cn('w-full justify-between px-2 text-sm font-normal', item.id === service.id && 'bg-signal/15 font-medium text-signal')}
        onClick={() => onSelect(item.id)}>
        <span className="truncate">{item.name}</span><Badge tone={item.online ? 'ok' : 'mist'}>{serviceStatus(item)}</Badge>
      </Button>)}
    </nav>
  </Card>
}

type CredentialAction = { type: 'issue'; previous?: ExternalMcpCredential } | { type: 'renew' | 'revoke'; credential: ExternalMcpCredential }
type Issued = ExternalMcpCredential & { token: string }

function CredentialDialog({ serviceId, action, close, onIssued }: {
  serviceId: string; action: CredentialAction; close: () => void; onIssued: (item: Issued) => void
}) {
  const client = useQueryClient()
  const previous = action.type === 'issue' ? action.previous : undefined
  const [name, setName] = useState(previous ? `${previous.name.slice(0, 93)}（轮换）` : '')
  const [duration, setDuration] = useState(action.type === 'issue' ? 'never' : '30')
  const days = duration === 'never' ? null : Number(duration)
  const save = useMutation({
    mutationFn: async () => {
      if (action.type === 'issue') return api.externalMcpIssue(serviceId, name.trim(), days, previous?.id)
      if (action.type === 'renew') await api.externalMcpRenew(serviceId, action.credential.id, days)
      else await api.externalMcpRevoke(serviceId, action.credential.id)
      return null
    },
    onSuccess: (item) => {
      void client.invalidateQueries({ queryKey: serviceKey })
      if (action.type === 'revoke') void client.invalidateQueries({ queryKey: ['mcp-catalog'] })
      if (item) onIssued(item)
      else toast.success(action.type === 'renew' ? '有效期已更新，Token 不变' : '凭证已销毁')
      close()
    },
  })
  const cancel = () => { if (!save.isPending) close() }
  const title = action.type === 'issue' ? previous ? '轮换凭证' : '签发注册凭证' : action.type === 'renew' ? '续期凭证' : '销毁凭证？'
  return <Dialog title={title} onClose={cancel} footer={<DialogActions close={cancel}>
    <Button type="submit" form="external-credential-form" variant={action.type === 'revoke' ? 'danger' : 'primary'}
      disabled={save.isPending || (action.type === 'issue' && !name.trim())}>
      {save.isPending ? '处理中…' : action.type === 'revoke' ? '确认销毁' : action.type === 'renew' ? '确认续期' : '签发凭证'}
    </Button>
  </DialogActions>}>
    <form id="external-credential-form" className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (!save.isPending) save.mutate() }}>
      {action.type === 'issue' ? <>
        <p className="text-sm text-mist">{previous ? '新凭证签发后，旧凭证仍然有效。请先切换外部部署，再销毁旧凭证。' : '此凭证仅用于 SDK 注册，不是客户端调用用的 MCP Key。'}</p>
        <Field label="凭证名称" htmlFor="external-credential-name"><Input id="external-credential-name" value={name} maxLength={100} required onChange={(event) => setName(event.target.value)} placeholder="例如：生产环境" /></Field>
      </> : <p className="text-sm text-mist">{action.type === 'renew'
        ? `「${action.credential.name}」当前到期：${expiry(action.credential.expires_at)}。续期不改变 Token。`
        : `销毁「${action.credential.name}」后，使用它注册的实例将立即下线。其他凭证和外部进程不受影响。`}</p>}
      {action.type !== 'revoke' ? <>
        <DurationSelect value={duration} onChange={setDuration} />
        <p className="text-xs text-mist">{days === null ? '凭证不会自动到期；不再使用或怀疑泄露时，请手动销毁。' : action.type === 'renew' ? '从原到期日延长，最晚延至今天起 90 天。' : `自签发之日起有效 ${days} 天。`}</p>
      </> : null}
      {save.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(save.error, '凭证操作失败')}</p> : null}
    </form>
  </Dialog>
}

function CredentialList({ service, canManage }: { service: ExternalMcpService; canManage: boolean }) {
  const query = useQuery({ queryKey: [...serviceKey, service.id, 'credentials'], queryFn: () => api.externalMcpCredentials(service.id) })
  const [action, setAction] = useState<CredentialAction | null>(null)
  const [issued, setIssued] = useState<Issued | null>(null)
  const [filter, setFilter] = useState('')
  const items = query.data?.items ?? []
  const visible = items.filter((item) => !filter || item.status === filter)
  return <Card className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="flex items-center gap-2 text-base font-semibold leading-6"><KeyRound size={16} aria-hidden="true" />注册凭证</h3>
        <p className="mt-0.5 text-sm leading-6 text-mist">用于 SDK 上线，明文只在签发时显示一次。</p></div>
      {canManage ? <Button type="button" onClick={() => setAction({ type: 'issue' })}><Plus size={15} />签发凭证</Button> : null}
    </div>
    {query.isPending ? <p role="status" className="text-sm text-mist">加载凭证中…</p> : null}
    {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载凭证失败')}</p> : null}
    {items.length ? <Select aria-label="筛选凭证状态" value={filter} onChange={(event) => setFilter(event.target.value)}>
      <option value="">全部状态</option>{Object.entries(statusText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </Select> : null}
    {query.isSuccess && !visible.length ? <EmptyState title={items.length ? '没有符合条件的凭证' : '还没有注册凭证'} description={items.length ? '切换状态筛选可查看其他凭证。' : '签发一枚凭证后，即可通过 SDK 注册此服务。'} /> : null}
    <div className="divide-y divide-line">
      {visible.map((item) => <div key={item.id} className="flex flex-col gap-2 py-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><span className="break-words text-sm font-medium leading-5 [overflow-wrap:anywhere]">{item.name}</span><Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{statusText[item.status]}</Badge></div>
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs leading-5 text-mist"><code>{item.prefix}…</code><span>{item.expires_at ? `到期 ${expiry(item.expires_at)}` : expiry(null)}</span><span>最近注册 {date(item.last_used_at)}</span></p>
        </div>
        {canManage && item.status !== 'revoked' ? <div className="flex shrink-0 flex-wrap gap-1.5">
          {item.status === 'active' && item.expires_at ? <Button variant="line" size="sm" onClick={() => setAction({ type: 'renew', credential: item })}>续期</Button> : null}
          <Button variant="line" size="sm" onClick={() => setAction({ type: 'issue', previous: item })}>轮换</Button>
          <Button variant="danger" size="sm" onClick={() => setAction({ type: 'revoke', credential: item })}>销毁</Button>
        </div> : null}
      </div>)}
    </div>
    {action ? <CredentialDialog serviceId={service.id} action={action} close={() => setAction(null)} onIssued={setIssued} /> : null}
    {issued ? <Dialog title="凭证已签发" onClose={() => setIssued(null)} footer={<div className="flex justify-end gap-2">
      <Button variant="ghost" onClick={() => setIssued(null)}>完成</Button>
      <Button onClick={() => void copyText(issued.token).then(() => toast.success('已复制')).catch(() => toast.error('请手动复制'))}><Copy size={15} />复制凭证</Button>
    </div>}>
      <p className="mb-3 text-sm text-mist">有效期：{expiry(issued.expires_at)}。关闭后无法再查看，请存入部署环境的密钥管理器。</p>
      <Input readOnly aria-label="注册凭证明文" className="font-mono" value={issued.token} />
    </Dialog> : null}
  </Card>
}

type ServiceAction = { type: 'create' } | { type: 'edit' | 'delete' | 'remove' | 'approve'; service: ExternalMcpService }

const serviceActionText = {
  create: ['创建外部服务', '创建服务'],
  edit: ['编辑外部服务', '保存名称'],
  delete: ['删除外部服务？', '确认删除'],
  remove: ['移除当前注册？', '确认移除'],
  approve: ['审核清单', '批准发布'],
} as const

export default function McpExternalPage() {
  const client = useQueryClient()
  const canManage = useCan('mcp_external:manage')
  const query = useQuery({ queryKey: serviceKey, queryFn: api.externalMcpServices, refetchInterval: 15_000 })
  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [action, setAction] = useState<ServiceAction | null>(null)
  const downloadKit = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/admin/mcp/external-services/integration-kit', { headers: webHeaders() })
      if (!response.ok || !response.headers.get('content-type')?.startsWith('application/zip')) {
        throw new Error(`下载接入包失败（${response.status}）`)
      }
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = 'external-mcp-sdk.zip'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
    onError: (error) => toast.error(errorMessage(error, '下载接入包失败')),
  })
  const services = query.data?.items ?? []
  const service = services.find((item) => item.id === selected) ?? services[0]
  const save = useMutation({
    mutationFn: async (target: ServiceAction) => {
      if (target.type === 'create') {
        const created = await api.externalMcpCreate(name.trim())
        client.setQueryData<typeof query.data>(serviceKey, (old) => ({ items: [...(old?.items ?? []), {
          ...created, online: false, online_instances: 0, last_seen_at: null, version: null, pending_revision: null, pending_manifest: null,
        }] }))
        setSelected(created.id)
      } else if (target.type === 'edit') {
        await api.externalMcpRename(target.service.id, name.trim())
        toast.success('服务名称已更新')
      } else if (target.type === 'delete') {
        await api.externalMcpDelete(target.service.id)
        client.setQueryData<typeof query.data>(serviceKey, (old) => ({ items: (old?.items ?? []).filter((item) => item.id !== target.service.id) }))
        setSelected('')
        toast.success('外部服务已删除')
      } else if (target.type === 'remove') await api.externalMcpRemove(target.service.id)
      else if (target.service.pending_revision) await api.externalMcpApprove(target.service.id, target.service.pending_revision)
    },
    onSuccess: () => {
      setAction(null)
      void client.invalidateQueries({ queryKey: serviceKey })
      void client.invalidateQueries({ queryKey: ['mcp-catalog'] })
      void client.invalidateQueries({ queryKey: ['mcp-key-integration'] })
    },
  })
  const open = (next: ServiceAction) => { save.reset(); setName(next.type === 'edit' ? next.service.name : ''); setAction(next) }
  const close = () => { if (!save.isPending) setAction(null) }
  const needsName = action?.type === 'create' || action?.type === 'edit'

  return <div className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0"><h2 className="text-xl font-semibold leading-7 tracking-tight">外部 MCP 服务</h2>
        <p className="mt-1 text-sm leading-6 text-mist">创建服务、签发凭证，再通过 SDK 注册上线。</p></div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="line" disabled={downloadKit.isPending} onClick={() => downloadKit.mutate()}>
          <Download size={15} />{downloadKit.isPending ? '正在下载…' : '下载接入包'}
        </Button>
        <Button type="button" variant="line" aria-label="刷新服务" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={15} className={query.isFetching ? 'motion-safe:animate-spin' : undefined} /></Button>
        {canManage ? <Button type="button" onClick={() => open({ type: 'create' })}><Plus size={15} />创建服务</Button> : null}
      </div>
    </header>
    {query.isPending ? <div role="status" aria-label="加载服务"><ListSkeleton rows={3} /><ListSkeleton rows={3} card={false} /></div> : null}
    {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载服务失败')}</p> : null}
    {!service && query.isSuccess ? <EmptyState title="还没有外部服务" description="创建服务后，为外部部署签发注册凭证。已接入的内置能力不受影响。" icon={<Server size={24} />} /> : null}
    {service ? <div className="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <ServiceNav services={services} service={service} onSelect={setSelected} />
      <div className="min-w-0 space-y-4">
        <Card className="space-y-3">
          <div className="flex flex-wrap items-center gap-2"><h3 className="min-w-0 break-words text-base font-semibold leading-6 [overflow-wrap:anywhere]">{service.name}</h3><Badge tone={service.online ? 'ok' : 'mist'}>{serviceStatus(service)}</Badge></div>
          <p className="break-all font-mono text-xs leading-5 text-mist">ext_{service.id}</p>
          <dl className="grid grid-cols-2 gap-3 text-sm leading-5 sm:grid-cols-3 [&_dd]:mt-0.5 [&_dd]:font-medium [&_dt]:text-xs [&_dt]:text-mist">
            <div><dt>在线实例</dt><dd className="tabular-nums">{service.online_instances}</dd></div>
            <div><dt>版本</dt><dd className="break-all">{service.version ?? '—'}</dd></div>
            <div><dt>最近心跳</dt><dd>{date(service.last_seen_at)}</dd></div>
          </dl>
          {canManage ? <div className="flex flex-wrap gap-2 border-t border-line pt-3">
            <Button variant="line" size="sm" onClick={() => open({ type: 'edit', service })}><Pencil size={14} />编辑名称</Button>
            {service.online ? <Button variant="line" size="sm" onClick={() => open({ type: 'remove', service })}>移除当前注册</Button> : null}
            {service.pending_revision ? <Button variant="line" size="sm" onClick={() => open({ type: 'approve', service })}>审阅待发布清单</Button> : null}
            <Button variant="danger" size="sm" onClick={() => open({ type: 'delete', service })}><Trash2 size={14} />删除服务</Button>
          </div> : null}
        </Card>
        <ExternalMcpHeartbeat key={`heartbeat-${service.id}`} serviceId={service.id} />
        <CredentialList key={service.id} service={service} canManage={canManage} />
      </div>
    </div> : null}
    {action ? <Dialog title={serviceActionText[action.type][0]} onClose={close}
      footer={<DialogActions close={close}><Button type="submit" form="external-service-form" variant={action.type === 'delete' ? 'danger' : 'primary'} disabled={save.isPending || (needsName && !name.trim())}>{save.isPending ? '处理中…' : serviceActionText[action.type][1]}</Button></DialogActions>}>
      <form id="external-service-form" className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!save.isPending) save.mutate(action) }}>
        {needsName ? <Field label="服务名称" htmlFor="external-service-name"><Input id="external-service-name" maxLength={100} required value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：文档检索服务" /></Field> : null}
        {action.type === 'edit' ? <p className="text-sm text-mist">只改变显示名称，不影响服务 ID、凭证或已有调用配置。</p> : null}
        {action.type === 'remove' ? <p className="text-sm text-mist">只清理「{action.service.name}」的当前注册，服务仍可使用原凭证再上线。</p> : null}
        {action.type === 'delete' ? <p className="text-sm text-mist">删除「{action.service.name}」会撤销其所有注册凭证，并从平台移除工具。外部进程不会被停止。若只是暂时清理注册，请使用「移除当前注册」。</p> : null}
        {action.type === 'approve' ? <p className="text-sm text-mist">批准后会替换当前版本，原有 MCP Key 授权仍适用。请审查新增或变更的工具。</p> : null}
        {action.type === 'approve' ? <pre className="whitespace-pre-wrap break-all rounded-md bg-ink p-3 text-xs">{JSON.stringify(action.service.pending_manifest, null, 2)}</pre> : null}
        {save.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(save.error, '操作失败')}</p> : null}
      </form>
    </Dialog> : null}
  </div>
}
