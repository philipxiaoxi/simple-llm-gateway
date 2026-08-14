import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api, type Account } from '../lib/api'
import { formatTime } from '../lib/utils'

export function AccountsPage() {
  const queryClient = useQueryClient()
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: api.providers })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('deepseek')
  const [apiKey, setApiKey] = useState('')
  const [message, setMessage] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [quotaText, setQuotaText] = useState<Record<number, string>>({})
  const [searchParams, setSearchParams] = useSearchParams()

  const preset = useMemo(() => providers.find((item) => item.id === provider), [providers, provider])

  useEffect(() => {
    const oauth = searchParams.get('oauth')
    if (!oauth) return
    if (oauth === 'ok') {
      setMessage('Grok 授权成功，凭证已保存。')
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } else {
      setMessage(`Grok 授权失败：${searchParams.get('reason') || '未知原因'}`)
    }
    searchParams.delete('oauth')
    searchParams.delete('reason')
    setSearchParams(searchParams, { replace: true })
  }, [queryClient, searchParams, setSearchParams])

  const createMutation = useMutation({
    mutationFn: () =>
      api.createAccount({
        name,
        provider,
        api_key: preset?.auth_type === 'api_key' ? apiKey : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      setCreating(false)
      setName('')
      setApiKey('')
    },
    onError: (error: Error) => setMessage(error.message),
  })

  async function runProbe(id: number) {
    setBusyId(id)
    try {
      const result = await api.probe(id)
      setMessage(result.ok ? `探测成功 ${result.latency_ms}ms` : `探测失败：${result.message}`)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '探测失败')
    } finally {
      setBusyId(null)
    }
  }

  async function runQuota(id: number) {
    setBusyId(id)
    try {
      const result = await api.quota(id)
      setQuotaText((current) => ({ ...current, [id]: JSON.stringify(result, null, 2) }))
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '额度查询失败')
    } finally {
      setBusyId(null)
    }
  }

  async function runModels(id: number) {
    setBusyId(id)
    try {
      const result = await api.models(id)
      if (!result.ok) {
        setMessage(result.message || '未能拉取模型列表')
        return
      }
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      setMessage(`已入库 ${result.models.length} 个模型`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '拉取模型失败')
    } finally {
      setBusyId(null)
    }
  }

  async function startOauth(id: number) {
    setBusyId(id)
    try {
      const result = await api.oauthStart(id)
      setMessage('正在打开 xAI 授权页。若没有跳转，请允许弹窗或点下面的链接。')
      const opened = window.open(result.authorize_url, '_blank', 'noopener,noreferrer')
      if (!opened) {
        window.location.href = result.authorize_url
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法开始授权')
    } finally {
      setBusyId(null)
    }
  }

  async function toggle(account: Account) {
    await api.updateAccount(account.id, { status: account.status === 'active' ? 'disabled' : 'active' })
    queryClient.invalidateQueries({ queryKey: ['accounts'] })
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">上游账号</h1>
          <p className="mt-1 text-sm text-mist">预设 OpenCode Go、Grok、DeepSeek。探测只在你点的时候发生。</p>
        </div>
        <Button onClick={() => setCreating((value) => !value)}>{creating ? '收起' : '新建账号'}</Button>
      </div>
      {message ? <div className="text-sm text-info">{message}</div> : null}
      {creating ? (
        <Card className="grid gap-3 md:grid-cols-2">
          <Field label="显示名">
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="DeepSeek 主号" />
          </Field>
          <Field label="供应商">
            <select
              className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              {providers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <div className="md:col-span-2 text-sm text-mist">Base URL：{preset?.base_url}</div>
          {preset?.auth_type === 'api_key' ? (
            <Field label="API Key">
              <Input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-..." />
            </Field>
          ) : (
            <div className="text-sm text-warn">创建后点「去授权」完成 Grok OAuth。</div>
          )}
          <div className="md:col-span-2">
            <Button disabled={!name || createMutation.isPending} onClick={() => createMutation.mutate()}>
              保存
            </Button>
          </div>
        </Card>
      ) : null}
      <div className="grid gap-3">
        {accounts.map((account) => (
          <Card key={account.id} className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-lg font-medium">{account.name}</div>
                <div className="font-mono text-xs text-mist">
                  {account.provider} · {account.base_url}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={account.status === 'active' ? 'ok' : 'mist'}>{account.status}</Badge>
                <Badge tone={account.has_credential ? 'info' : 'warn'}>
                  {account.has_credential ? '已配置凭证' : '缺凭证'}
                </Badge>
                {account.last_probe_ok === true ? <Badge tone="ok">探测正常</Badge> : null}
                {account.last_probe_ok === false ? <Badge tone="bad">探测失败</Badge> : null}
              </div>
            </div>
            <div className="text-sm text-mist">
              上次探测：{formatTime(account.last_probe_at)}
              {account.last_probe_latency_ms != null ? ` · ${account.last_probe_latency_ms}ms` : ''}
              {account.last_probe_message ? ` · ${account.last_probe_message}` : ''}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="line" disabled={busyId === account.id} onClick={() => runProbe(account.id)}>
                探测
              </Button>
              <Button variant="line" disabled={busyId === account.id} onClick={() => runQuota(account.id)}>
                刷新额度
              </Button>
              <Button type="button" variant="line" disabled={busyId === account.id} onClick={() => runModels(account.id)}>
                获取模型
              </Button>
              {account.provider === 'grok' ? (
                <Button type="button" variant="line" disabled={busyId === account.id} onClick={() => startOauth(account.id)}>
                  去授权
                </Button>
              ) : null}
              <Button variant="ghost" onClick={() => toggle(account)}>
                {account.status === 'active' ? '停用' : '启用'}
              </Button>
            </div>
            <div>
              <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">
                模型{account.models_updated_at ? ` · ${formatTime(account.models_updated_at)}` : ''}
              </div>
              {account.models?.length ? (
                <div className="flex flex-wrap gap-2">
                  {account.models.map((modelName) => (
                    <Badge key={modelName} tone="info">
                      {modelName}
                    </Badge>
                  ))}
                </div>
              ) : (
                <div className="text-sm text-mist">还没有模型，点「获取模型」从上游拉取并入库。</div>
              )}
            </div>
            {quotaText[account.id] ? (
              <pre className="overflow-auto rounded-md bg-ink p-3 font-mono text-xs text-mist">{quotaText[account.id]}</pre>
            ) : null}
          </Card>
        ))}
      </div>
    </div>
  )
}
