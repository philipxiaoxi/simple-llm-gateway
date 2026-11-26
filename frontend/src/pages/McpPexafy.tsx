import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'

const settingsKey = ['mcp-pexafy-settings']
const usageKey = ['mcp-pexafy-usage']

export default function McpPexafyPage() {
  const client = useQueryClient()
  const canConfigure = useCan('mcp_pexafy:configure')
  const [key, setKey] = useState('')
  const settings = useQuery({ queryKey: settingsKey, queryFn: api.pexafySettings })
  const usage = useQuery({ queryKey: usageKey, queryFn: api.pexafyUsage, enabled: false, retry: false })
  const catalog = useQuery({ queryKey: ['mcp-catalog', 'plaza'], queryFn: () => api.mcpCatalog('plaza') })
  const tools = catalog.data?.items.find(item => item.capability_id === 'pexafy')?.tools ?? []
  const save = useMutation({
    mutationFn: api.savePexafySettings,
    onSuccess: data => {
      client.setQueryData(settingsKey, data)
      client.removeQueries({ queryKey: usageKey })
      setKey('')
      notifyOk(data.configured ? 'Pexafy Key 已保存' : 'Pexafy Key 已清除')
    },
    onError: error => notifyBad(errorMessage(error, '保存失败')),
  })
  const configured = settings.data?.configured
  const quota = usage.data?.data
  return <PageContainer width="form" className="space-y-4">
    <PageHeader title="Pexafy 图片搜索" backTo="/mcp-plaza" backLabel="服务目录"
      description="图片素材搜索与 XAPI 代理下载，适用于 PPT、文章等配图。"
      titleBadge={settings.isSuccess ? <Badge tone={configured ? 'ok' : 'warn'}>{configured ? '已配置' : '未配置'}</Badge> : null} />
    <Card className="space-y-4">
      <h2 className="text-base font-semibold">API Key</h2>
      <p className="text-sm text-mist">密钥加密保存，不向 MCP 客户端公开。<a className="text-signal" href="https://pexafy.com/dashboard/api-keys/create/" target="_blank" rel="noreferrer"> 获取 API Key</a></p>
      {settings.isError ? <p className="text-sm text-danger">{errorMessage(settings.error, '加载配置失败')}</p> : null}
      {canConfigure && settings.isSuccess ? <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (key.trim()) save.mutate(key.trim()) }}>
        <Field label="API Key" htmlFor="pexafy-key"><Input id="pexafy-key" type="password" autoComplete="new-password" value={key} placeholder={configured ? '已配置，输入新 Key 可替换' : '输入 Pexafy API Key'} onChange={event => setKey(event.target.value)} disabled={save.isPending} /></Field>
        <div className="flex gap-2">
          <Button type="submit" disabled={!key.trim() || save.isPending}>保存</Button>
          {configured ? <Button type="button" variant="danger" disabled={save.isPending} onClick={() => { if (window.confirm('确认清除 Pexafy Key？')) save.mutate(null) }}>清除</Button> : null}
        </div>
      </form> : null}
    </Card>
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2"><h2 className="text-base font-semibold">月度额度</h2><Button variant="secondary" disabled={!configured || usage.isFetching || save.isPending} onClick={() => void usage.refetch()}>{usage.isFetching ? '查询中…' : '测试连接 / 刷新'}</Button></div>
      {configured && usage.isError ? <p className="text-sm text-danger">{errorMessage(usage.error, '连接失败')}</p> : null}
      {configured && quota ? <div className="space-y-2 text-sm"><p className="text-mist">连接成功 · {quota.period}</p><div className="grid grid-cols-3 gap-2">{[['总额度', quota.requests.limit], ['已使用', quota.requests.used], ['剩余', quota.requests.remaining]].map(([label, value]) => <div key={label} className="rounded-md border border-line p-3"><p className="text-xs text-mist">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>)}</div></div> : <p className="text-sm text-mist">点击测试连接查看当前月额度。</p>}
    </Card>
    <Card className="space-y-3"><h2 className="text-base font-semibold">MCP 工具</h2><p className="text-sm text-mist">在 MCP Key 中授权本能力即可调用。下载链接有效期 15 分钟，请及时保存图片。</p>{tools.map(tool => <div key={tool.name} className="border-t border-line pt-2"><code className="text-sm text-paper">{tool.name}</code><p className="text-xs text-mist">{tool.description}</p></div>)}</Card>
  </PageContainer>
}
