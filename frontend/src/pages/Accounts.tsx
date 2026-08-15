import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type Account, type Provider, type QuotaItem } from '../lib/api'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { formatTime } from '../lib/utils'

function AccountEditor({
  account,
  providers,
  onClose,
  onSaved,
}: {
  account: Account | null
  providers: Provider[]
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const editing = account !== null
  const [name, setName] = useState(account?.name ?? '')
  const [provider, setProvider] = useState(account?.provider ?? 'deepseek')
  const [baseUrl, setBaseUrl] = useState(account?.base_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  const preset = useMemo(() => providers.find((item) => item.id === provider), [providers, provider])
  const authType = editing ? account.auth_type : preset?.auth_type

  useEffect(() => {
    if (editing) return
    if (preset?.base_url) setBaseUrl(preset.base_url)
  }, [editing, preset?.base_url])

  async function save() {
    const trimmedName = name.trim()
    const trimmedUrl = baseUrl.trim()
    if (!trimmedName) {
      setError('请填写显示名')
      return
    }
    setPending(true)
    setError('')
    try {
      if (editing) {
        await api.updateAccount(account.id, {
          name: trimmedName,
          base_url: trimmedUrl || undefined,
          api_key: authType === 'api_key' && apiKey.trim() ? apiKey.trim() : undefined,
        })
        onSaved('账号已更新')
      } else {
        await api.createAccount({
          name: trimmedName,
          provider,
          base_url: trimmedUrl || undefined,
          api_key: authType === 'api_key' ? apiKey : undefined,
        })
        onSaved('账号已创建')
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title={editing ? '编辑账号' : '新建账号'} onClose={onClose}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="显示名">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="DeepSeek 主号" />
        </Field>
        <Field label="供应商">
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm disabled:opacity-60"
            value={provider}
            disabled={editing}
            onChange={(event) => setProvider(event.target.value)}
          >
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="sm:col-span-2">
          <Field label="Base URL">
            <Input
              className="font-mono"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder={preset?.base_url || 'https://...'}
            />
          </Field>
        </div>
        {authType === 'api_key' ? (
          <div className="sm:col-span-2">
            <Field label="API Key">
              <Input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={editing ? '不改请留空' : 'sk-...'}
              />
            </Field>
          </div>
        ) : (
          <div className="sm:col-span-2 text-sm text-warn">
            {editing ? 'OAuth 授权请在卡片上点「去授权」。' : '创建后点「去授权」完成 Grok OAuth。'}
          </div>
        )}
        {error ? <div className="sm:col-span-2 text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={!name.trim() || pending} onClick={() => void save()}>
            保存
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function ExportDialog({ onClose }: { onClose: () => void }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit() {
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    if (password !== confirm) {
      setError('两次密码不一致')
      return
    }
    setPending(true)
    setError('')
    try {
      const envelope = await api.exportAccounts(password)
      const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'upstream-accounts.json'
      link.click()
      URL.revokeObjectURL(url)
      notifyOk('已导出加密 JSON')
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '导出失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="导出上游账号" onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-sm text-mist">导出全部上游账号。请设置至少 8 位密码，导入时要用同一密码解密。Grok 号不含授权，到新环境需重新点「去授权」。</p>
        <Field label="密码">
          <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        <Field label="确认密码">
          <Input type="password" value={confirm} onChange={(event) => setConfirm(event.target.value)} />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void submit()}>
            导出
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: () => void
}) {
  const [password, setPassword] = useState('')
  const [fileText, setFileText] = useState('')
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function onFile(event: { target: { files: FileList | null } }) {
    const file = event.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    setFileText(await file.text())
  }

  async function submit() {
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    if (!fileText.trim()) {
      setError('请选择导出的 JSON 文件')
      return
    }
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(fileText) as Record<string, unknown>
    } catch {
      setError('文件不是合法 JSON')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.importAccounts(password, payload)
      notifyOk(`已导入 ${result.created} 个账号${result.skipped ? `，跳过 ${result.skipped} 个` : ''}`)
      onImported()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '导入失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="导入上游账号" onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-sm text-mist">选择加密导出的 JSON，输入当时的密码。重名会新建为「原名（1）」。</p>
        <Field label="JSON 文件">
          <input type="file" accept="application/json,.json" onChange={(event) => void onFile(event)} />
        </Field>
        {fileName ? <div className="font-mono text-xs text-mist">{fileName}</div> : null}
        <Field label="密码">
          <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void submit()}>
            导入
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function QuotaItems({ items }: { items: QuotaItem[] }) {
  return (
    <div className="space-y-3">
      {items.map((item, index) => {
        if (item.type === 'progress') {
          const used = Math.min(Math.max(Number(item.value) || 0, 0), 100)
          const tone = used >= 90 ? 'bg-danger' : used >= 70 ? 'bg-warn' : 'bg-signal'
          return (
            <div key={`${item.label}-${index}`} className="space-y-1.5">
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
                <span>{item.label}</span>
                <span className="font-mono text-xs text-mist">{used}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-ink">
                <div className={`h-full rounded-full ${tone}`} style={{ width: `${used}%` }} />
              </div>
            </div>
          )
        }
        return (
          <div key={`${item.label}-${index}`} className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span>{item.label}</span>
            <span className="font-mono text-xs text-mist">{String(item.value)}</span>
          </div>
        )
      })}
    </div>
  )
}

export function AccountsPage() {
  const queryClient = useQueryClient()
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: api.providers })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const [editor, setEditor] = useState<Account | 'new' | null>(null)
  const [transfer, setTransfer] = useState<'export' | 'import' | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [oauthPaste, setOauthPaste] = useState('')
  const [oauthDialog, setOauthDialog] = useState(false)
  const [oauthAccountId, setOauthAccountId] = useState<number | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()

  useEffect(() => {
    const oauth = searchParams.get('oauth')
    if (!oauth) return
    if (oauth === 'ok') {
      notifyOk('Grok 授权成功，凭证已保存。')
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } else {
      notifyBad(`Grok 授权失败：${searchParams.get('reason') || '未知原因'}`)
    }
    searchParams.delete('oauth')
    searchParams.delete('reason')
    setSearchParams(searchParams, { replace: true })
  }, [queryClient, searchParams, setSearchParams])

  async function runProbe(id: number) {
    setBusyId(id)
    try {
      const result = await api.probe(id)
      if (result.ok) notifyOk(`探测成功 ${result.latency_ms}ms`)
      else notifyBad(`探测失败：${result.message}`)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } catch (error) {
      notifyBad(error instanceof Error ? error.message : '探测失败')
    } finally {
      setBusyId(null)
    }
  }

  async function runQuota(id: number) {
    setBusyId(id)
    try {
      const result = await api.quota(id)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      if (result.ok === false) notifyBad(result.message || '额度查询失败')
      else notifyOk('额度已更新')
    } catch (error) {
      notifyBad(error instanceof Error ? error.message : '额度查询失败')
    } finally {
      setBusyId(null)
    }
  }

  async function runModels(id: number) {
    setBusyId(id)
    try {
      const result = await api.models(id)
      if (!result.ok) {
        notifyBad(result.message || '未能拉取模型列表')
        return
      }
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      notifyOk(`已入库 ${result.models.length} 个模型`)
    } catch (error) {
      notifyBad(error instanceof Error ? error.message : '拉取模型失败')
    } finally {
      setBusyId(null)
    }
  }

  async function startOauth(id: number) {
    setBusyId(id)
    try {
      const result = await api.oauthStart(id)
      const popup = window.open(result.authorize_url, '_blank')
      if (popup) popup.opener = null
      if (result.needs_paste) {
        setOauthPaste('')
        setOauthAccountId(id)
        setOauthDialog(true)
        notifyInfo(popup ? '已打开 xAI 授权页。确认后把页面上的代码或 API Key 粘回来。' : '弹窗被拦截，请允许后再点一次，或手动打开授权页。')
      } else {
        notifyInfo(popup ? '已打开 xAI 授权页，完成授权后会回到本站。' : '弹窗被拦截，请允许弹窗后再点一次「去授权」。')
      }
    } catch (error) {
      notifyBad(error instanceof Error ? error.message : '无法开始授权')
    } finally {
      setBusyId(null)
    }
  }

  async function toggle(account: Account) {
    await api.updateAccount(account.id, { status: account.status === 'active' ? 'disabled' : 'active' })
    queryClient.invalidateQueries({ queryKey: ['accounts'] })
  }

  async function removeAccount(account: Account) {
    setBusyId(account.id)
    try {
      await api.deleteAccount(account.id)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      notifyOk(`已删除 ${account.name}`)
    } catch (error) {
      notifyBad(error instanceof Error ? error.message : '删除失败')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">上游账号</h1>
          <p className="mt-1 text-sm text-mist">
            预设 OpenCode Go、Grok、DeepSeek，也可选通用 OpenAI / Anthropic。探测只在你点的时候发生。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="line" onClick={() => setTransfer('export')}>
            导出
          </Button>
          <Button type="button" variant="line" onClick={() => setTransfer('import')}>
            导入
          </Button>
          <Button onClick={() => setEditor('new')}>新建账号</Button>
        </div>
      </div>
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
              {account.auth_type === 'oauth' ? (
                <Button type="button" variant="line" disabled={busyId === account.id} onClick={() => startOauth(account.id)}>
                  去授权
                </Button>
              ) : null}
              <Button variant="line" onClick={() => setEditor(account)}>
                编辑
              </Button>
              <Button variant="ghost" onClick={() => toggle(account)}>
                {account.status === 'active' ? '停用' : '启用'}
              </Button>
              <Button variant="danger" disabled={busyId === account.id} onClick={() => void removeAccount(account)}>
                删除
              </Button>
            </div>
            <div>
              <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">
                额度{account.quota_updated_at ? ` · ${formatTime(account.quota_updated_at)}` : ''}
              </div>
              {account.quota?.items?.length ? (
                <QuotaItems items={account.quota.items} />
              ) : (
                <div className="text-sm text-mist">{account.quota?.message || '还没有额度，点「刷新额度」拉取。'}</div>
              )}
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
          </Card>
        ))}
      </div>
      {transfer === 'export' ? <ExportDialog onClose={() => setTransfer(null)} /> : null}
      {transfer === 'import' ? (
        <ImportDialog
          onClose={() => setTransfer(null)}
          onImported={() => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
          }}
        />
      ) : null}
      {editor !== null ? (
        <AccountEditor
          account={editor === 'new' ? null : editor}
          providers={providers}
          onClose={() => setEditor(null)}
          onSaved={(text) => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
            setEditor(null)
            notifyOk(text)
          }}
        />
      ) : null}
      {oauthDialog ? (
        <Dialog title="完成 Grok 授权" onClose={() => setOauthDialog(false)}>
          <div className="space-y-3">
            <p className="text-sm text-mist">
              登录后页面常常不会跳到 127.0.0.1，而是停在授权页并给出一段代码或 API Key。把
              <span className="text-paper"> 页面上显示的那段 </span>
              粘到下面，不要粘授权页网址本身。如果地址栏已经变成
              <span className="font-mono text-paper"> 127.0.0.1:56121/callback?...</span>
              ，也可以粘完整链接。粘授权码或回调链接才能自动续期；粘
              <span className="font-mono text-paper"> xai- </span>
              API Key 不会过期，也没有 refresh。
            </p>
            <Field label="授权码 / API Key / 回调链接">
              <Input
                value={oauthPaste}
                onChange={(event) => setOauthPaste(event.target.value)}
                placeholder="xai-... 或授权码，或 http://127.0.0.1:56121/callback?code=..."
              />
            </Field>
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={() => setOauthDialog(false)}>
                取消
              </Button>
              <Button
                type="button"
                disabled={!oauthPaste.trim() || oauthAccountId == null}
                onClick={async () => {
                  try {
                    await api.completeOauth({
                      account_id: oauthAccountId ?? undefined,
                      callback_url: oauthPaste.trim(),
                    })
                    queryClient.invalidateQueries({ queryKey: ['accounts'] })
                    setOauthDialog(false)
                    setOauthPaste('')
                    setOauthAccountId(null)
                    notifyOk('Grok 授权成功，凭证已保存。')
                  } catch (error) {
                    notifyBad(error instanceof Error ? error.message : '兑换授权失败')
                  }
                }}
              >
                完成授权
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}
