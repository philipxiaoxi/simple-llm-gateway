import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, KeyRound, Plus, RotateCw, Server, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, Dialog, Input, Select } from '../components/ui'
import { Label } from '../components/ui/label'
import { api, type ExternalMcpCredential, type ExternalMcpService } from '../lib/api'
import { useCan } from '../lib/session'
import { copyText, errorMessage } from '../lib/utils'

const date = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
const serviceKey = ['mcp-external']
const statusText = { active: '有效', expired: '已过期', revoked: '已销毁' }

function CredentialManager({ service, canManage }: { service: ExternalMcpService; canManage: boolean }) {
  const client = useQueryClient()
  const key = [...serviceKey, service.id, 'credentials']
  const query = useQuery({ queryKey: key, queryFn: () => api.externalMcpCredentials(service.id), refetchInterval: 15_000 })
  const [name, setName] = useState('')
  const [days, setDays] = useState(30)
  const [rotation, setRotation] = useState<ExternalMcpCredential | null>(null)
  const [issued, setIssued] = useState('')
  const [revoke, setRevoke] = useState<ExternalMcpCredential | null>(null)
  const [filter, setFilter] = useState('')
  const mutation = useMutation({
    mutationFn: async (action: { type: 'issue' } | { type: 'renew' | 'revoke'; id: string }) => {
      if (action.type === 'issue') {
        const result = await api.externalMcpIssue(service.id, name.trim(), days, rotation?.id)
        setIssued(result.token)
        setName('')
        setRotation(null)
      } else if (action.type === 'renew') {
        await api.externalMcpRenew(service.id, action.id, days)
        toast.success('凭证已续期，Token 不变')
      } else {
        await api.externalMcpRevoke(service.id, action.id)
        setRevoke(null)
        toast.success('凭证已销毁')
      }
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: serviceKey }),
    onError: (error) => toast.error(errorMessage(error, '凭证操作失败')),
  })

  return (
    <Card className="space-y-4">
      <h3 className="flex items-center gap-2 font-medium"><KeyRound size={17} />注册凭证 · {service.name}</h3>
      <p className="text-sm text-mist">仅供外部 SDK 注册，不是用户调用工具所用的 MCP Key。明文只在签发时显示一次。</p>
      {canManage ? (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); mutation.mutate({ type: 'issue' }) }}>
          <div className="space-y-1"><Label htmlFor="external-credential-name">{rotation ? `替换 ${rotation.name}` : '凭证名称'}</Label><Input id="external-credential-name" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="external-credential-duration">签发／续期时长</Label><Select id="external-credential-duration" value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {[7, 30, 90].map((value) => <option key={value} value={value}>{value} 天</option>)}
          </Select></div>
          <Button type="submit" disabled={!name.trim() || mutation.isPending || Boolean(issued)}><Plus size={15} />{rotation ? '签发替代凭证' : '签发凭证'}</Button>
          {rotation ? <Button type="button" variant="ghost" onClick={() => setRotation(null)}>取消轮换</Button> : null}
        </form>
      ) : null}
      <Select aria-label="凭证状态" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="">全部状态</option>
        {Object.entries(statusText).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </Select>
      {query.isPending ? <p role="status" className="text-sm text-mist">加载凭证中…</p> : null}
      {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载失败')}</p> : null}
      {(query.data?.items ?? []).filter((item) => !filter || item.status === filter).map((item) => (
        <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-3">
          <div><div className="flex items-center gap-2">{item.name}<Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{statusText[item.status]}</Badge></div>
            <p className="text-xs text-mist">{item.prefix}… · 签发 {date(item.created_at)}（管理员 #{item.created_by}） · 到期 {date(item.expires_at)} · 最近注册 {date(item.last_used_at)}</p></div>
          {canManage && item.status !== 'revoked' ? <div className="flex flex-wrap gap-2">
            {item.status === 'active' ? <Button size="sm" variant="line" disabled={mutation.isPending} onClick={() => mutation.mutate({ type: 'renew', id: item.id })}>续期</Button> : null}
            <Button size="sm" variant="line" onClick={() => { setRotation(item); setName(`${item.name.slice(0, 93)}（轮换）`) }}><RotateCw size={13} />轮换</Button>
            <Button size="sm" variant="danger" onClick={() => setRevoke(item)}><Trash2 size={13} />销毁</Button>
          </div> : null}
        </div>
      ))}
      {issued ? <Dialog title="复制注册凭证" onClose={() => setIssued('')}>
        <p className="mb-3 text-sm text-mist">关闭后不能再次查看明文。轮换时请先让新凭证接入成功，再销毁旧凭证。</p>
        <Input readOnly aria-label="注册凭证明文" value={issued} />
        <Button className="mt-3" onClick={() => void copyText(issued).then(() => toast.success('已复制')).catch(() => toast.error('请手动复制'))}><Copy size={15} />复制凭证</Button>
      </Dialog> : null}
      {revoke ? <Dialog title="销毁注册凭证？" onClose={() => setRevoke(null)}>
        <p className="text-sm text-mist">「{revoke.name}」和使用它注册的实例将失效；不影响其他凭证或外部 MCP 进程。</p>
        <Button className="mt-4" variant="danger" disabled={mutation.isPending} onClick={() => mutation.mutate({ type: 'revoke', id: revoke.id })}>确认销毁</Button>
      </Dialog> : null}
    </Card>
  )
}

export default function McpExternalPage() {
  const client = useQueryClient()
  const canManage = useCan('mcp_external:manage')
  const query = useQuery({ queryKey: serviceKey, queryFn: api.externalMcpServices, refetchInterval: 15_000 })
  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [dialog, setDialog] = useState<'remove' | 'approve' | null>(null)
  const services = query.data?.items ?? []
  const service = services.find((item) => item.id === selected) ?? services[0]
  const mutation = useMutation({
    mutationFn: async (action: 'create' | 'remove' | 'approve') => {
      if (action === 'create') {
        await api.externalMcpCreate(name.trim())
        setName('')
      } else if (service && action === 'remove') {
        await api.externalMcpRemove(service.id)
      } else if (service?.pending_revision) {
        await api.externalMcpApprove(service.id, service.pending_revision)
      }
    },
    onSuccess: () => {
      setDialog(null)
      void client.invalidateQueries({ queryKey: serviceKey })
      void client.invalidateQueries({ queryKey: ['mcp-catalog'] })
    },
    onError: (error) => toast.error(errorMessage(error, '操作失败')),
  })

  return <div className="space-y-4">
    <Card className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold"><Server size={18} />外部 MCP 服务</h2>
      <p className="text-sm text-mist">先创建服务并签发注册凭证，再通过 SDK 上线。用户继续用平台 MCP Key 授权和调用。</p>
      {canManage ? <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); mutation.mutate('create') }}>
        <Input className="w-auto" aria-label="服务名称" placeholder="服务名称" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} />
        <Button type="submit" disabled={!name.trim() || mutation.isPending}><Plus size={15} />创建服务</Button>
      </form> : null}
      {query.isPending ? <p role="status" className="text-sm text-mist">加载服务中…</p> : null}
      {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载失败')}</p> : null}
      {service ? <>
        <Select aria-label="选择外部服务" value={service.id} onChange={(event) => { setSelected(event.target.value); setDialog(null) }}>
          {services.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.online ? '在线' : '离线'}</option>)}
        </Select>
        <div className="flex flex-wrap items-center gap-2 text-sm text-mist">
          <Badge tone={service.online ? 'ok' : 'mist'}>{service.online ? '在线' : service.version ? '离线' : '未注册'}</Badge>
          <span>实例 {service.online_instances} · 版本 {service.version ?? '—'} · 最近心跳 {date(service.last_seen_at)}</span>
        </div>
        {canManage ? <div className="flex flex-wrap gap-2">
          <Button variant="line" onClick={() => setDialog('remove')}>移除当前注册</Button>
          {service.pending_revision ? <Button variant="line" onClick={() => setDialog('approve')}>审阅待发布清单</Button> : null}
        </div> : null}
      </> : query.data ? <p className="text-sm text-mist">还没有外部服务。</p> : null}
    </Card>
    {service ? <CredentialManager key={service.id} service={service} canManage={canManage} /> : null}
    {dialog ? <Dialog title={dialog === 'remove' ? '移除当前注册？' : '审核清单'} onClose={() => setDialog(null)}>
      <p className="text-sm text-mist">{dialog === 'remove' ? '仅清理平台上的当前实例，不撤销凭证。外部进程仍在运行时，可在下次心跳后自动注册。' : '批准后此清单将替换当前版本，原有 MCP Key 授权仍适用。请审查新增或变更的工具。'}</p>
      {dialog === 'approve' ? <pre className="mt-3 max-h-60 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(service?.pending_manifest, null, 2)}</pre> : null}
      <Button className="mt-4" disabled={mutation.isPending} onClick={() => mutation.mutate(dialog)}>{dialog === 'remove' ? '确认移除' : '批准发布'}</Button>
    </Dialog> : null}
  </div>
}
