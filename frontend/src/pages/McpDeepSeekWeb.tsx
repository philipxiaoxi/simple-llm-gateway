import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Field, Select } from '../components/ui'
import { api } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'

const settingsKey = ['mcp-deepseek-web-settings']

export default function McpDeepSeekWebPage() {
  const client = useQueryClient()
  const canConfigure = useCan('mcp_deepseek:configure')
  const query = useQuery({ queryKey: settingsKey, queryFn: api.deepseekWebSettings })
  const [draft, setDraft] = useState<string | null>(null)
  const saved = query.data?.account_id == null ? '' : String(query.data.account_id)
  const selected = draft ?? saved
  const accounts = query.data?.accounts ?? []
  const usable = accounts.some((account) => account.available && String(account.id) === saved)
  const save = useMutation({
    mutationFn: api.saveDeepseekWebSettings,
    onSuccess: (data) => {
      client.setQueryData<typeof query.data>(settingsKey, (old) => old ? { ...old, account_id: data.account_id } : old)
      setDraft(null)
      notifyOk(data.account_id ? 'DeepSeek 上游账号已绑定' : '已解除上游账号绑定')
    },
    onError: (error) => notifyBad(errorMessage(error, '保存失败')),
  })

  return <PageContainer width="form" className="space-y-4">
    <PageHeader title="DeepSeek 网页搜索" backTo="/mcp-plaza" backLabel="服务目录"
      description="通过平台 MCP Key 搜索公开网页，或读取网页的 Markdown 正文。"
      titleBadge={query.isSuccess ? <Badge tone={usable ? 'ok' : 'mist'}>{usable ? '已绑定账号' : '未配置可用账号'}</Badge> : null} />
    <Card className="space-y-4">
      <div><h2 className="text-base font-semibold">上游账号</h2><p className="mt-1 text-sm text-mist">仅列出供应商为 DeepSeek 的上游账号。密钥由平台保管，不会发给 MCP 客户端。</p></div>
      {query.isPending ? <p role="status" className="text-sm text-mist">加载账号中…</p> : null}
      {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载账号失败')}</p> : null}
      {query.isSuccess ? <form className="space-y-3" onSubmit={(event) => {
        event.preventDefault()
        if (canConfigure && !save.isPending && selected !== saved) save.mutate(selected ? Number(selected) : null)
      }}>
        <Field label="DeepSeek 上游账号" htmlFor="deepseek-web-account">
          <Select id="deepseek-web-account" value={selected} disabled={!canConfigure || save.isPending} onChange={(event) => setDraft(event.target.value)}>
            <option value="">不绑定</option>
            {accounts.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.name}{item.available ? '' : '（不可用）'}</option>)}
          </Select>
        </Field>
        {!accounts.some((item) => item.available) ? <p className="text-sm text-mist">请先在「上游账号」中添加或启用一个已配置密钥的 DeepSeek 账号。</p> : null}
        {canConfigure ? <Button type="submit" disabled={selected === saved || save.isPending}>{save.isPending ? '保存中…' : '保存绑定'}</Button> : null}
      </form> : null}
    </Card>
    <Card className="space-y-2 text-sm text-mist">
      <h2 className="text-base font-semibold text-paper">工具与授权</h2>
      <p><code>deepseek_search</code> 搜索公开网页；<code>deepseek_fetch</code> 读取网页正文。</p>
      <p>在 MCP Key 中勾选「DeepSeek 网页搜索」后，通过该 Key 的「接入」查看 MCP 和 REST 示例。成功且有结果的请求按 DeepSeek 官方规则计费。</p>
    </Card>
  </PageContainer>
}
