import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Field, Select } from '../components/ui'
import { api } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'

const settingsKey = ['mcp-deepseek-web-settings']
const capabilities = {
  'deepseek-web': { title: 'DeepSeek 网页搜索', description: '搜索公开网页，或读取网页的 Markdown 正文。', tools: 'deepseek_search、deepseek_fetch' },
  'deepseek-asr': { title: 'DeepSeek 语音识别', description: '将完整音频转写为文字，不支持流式识别。', tools: 'deepseek_asr' },
  'deepseek-tts': { title: 'DeepSeek 语音合成', description: '将整段文字合成为 WAV 或 PCM 音频。', tools: 'deepseek_tts' },
  'deepseek-ocr': { title: 'DeepSeek OCR', description: '识别文档或图片中的文字，按页返回。', tools: 'deepseek_ocr' },
} as const

export default function McpDeepSeekWebPage() {
  const path = useLocation().pathname.split('/').at(-1) as keyof typeof capabilities
  const capability = capabilities[path] ?? capabilities['deepseek-web']
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
    <PageHeader title={capability.title} backTo="/mcp-plaza" backLabel="服务目录"
      description={capability.description}
      titleBadge={query.isSuccess ? <Badge tone={usable ? 'ok' : 'mist'}>{usable ? '已绑定账号' : '未配置可用账号'}</Badge> : null} />
    <Card className="space-y-4">
      <div><h2 className="text-base font-semibold">上游账号</h2><p className="mt-1 text-sm text-mist">网页搜索、语音识别、语音合成和 OCR 共用此账号。仅列出 DeepSeek 上游账号，不会向 MCP 客户端暴露密钥。</p></div>
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
      <p><code>{capability.tools}</code></p>
      <p>在 MCP Key 中勾选「{capability.title}」后，通过该 Key 的「接入」查看 MCP 和 REST 示例。调用按 DeepSeek 官方规则计费。</p>
    </Card>
  </PageContainer>
}
