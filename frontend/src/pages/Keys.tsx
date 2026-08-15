import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type CcSwitchTarget } from '../lib/api'
import { formatTime } from '../lib/utils'

export function KeysPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const { data: keys = [] } = useQuery({ queryKey: ['keys'], queryFn: api.keys })
  const [name, setName] = useState('')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [revealed, setRevealed] = useState<Record<number, string>>({})
  const [ccPanel, setCcPanel] = useState<Record<number, { models: string[]; targets: CcSwitchTarget[] }>>({})
  const [dialog, setDialog] = useState<{
    keyId: number
    app: string
    label: string
    models: string[]
    model: string
    haiku: string
    sonnet: string
    opus: string
  } | null>(null)
  const [message, setMessage] = useState('')

  const createMutation = useMutation({
    mutationFn: () => api.createKey({ name, account_id: Number(accountId) }),
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ['keys'] })
      if (item.key) setRevealed((current) => ({ ...current, [item.id]: item.key as string }))
      setName('')
      setMessage('已创建，完整 Key 显示在对应卡片上。可把「分享页」和 Key 发给对方自行导入。')
    },
    onError: (error: Error) => setMessage(error.message),
  })

  async function showKey(id: number) {
    const item = await api.key(id)
    if (item.key) setRevealed((current) => ({ ...current, [id]: item.key as string }))
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text)
    setMessage('已复制')
  }

  async function loadCcSwitch(id: number) {
    try {
      const result = await api.ccSwitch(id)
      setCcPanel((current) => ({ ...current, [id]: { models: result.models, targets: result.targets } }))
      if (result.models.length === 0) {
        setMessage('绑定账号还没有模型列表，请先到「上游账号」点「获取模型」。')
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法生成 CC Switch 链接')
    }
  }

  function openCcTarget(keyId: number, target: CcSwitchTarget, models: string[]) {
    if (!target.needs_dialog) {
      if (target.url) {
        setMessage(`正在打开 CC Switch（${target.label}）。`)
        window.location.href = target.url
      }
      return
    }
    if (models.length === 0) {
      setMessage('绑定账号还没有模型，请先到「上游账号」点「获取模型」。')
      return
    }
    setDialog({
      keyId,
      app: target.app,
      label: target.label,
      models,
      model: models[0],
      haiku: '',
      sonnet: '',
      opus: '',
    })
  }

  async function confirmDialog() {
    if (!dialog) return
    try {
      const result = await api.ccSwitchBuild(dialog.keyId, {
        app: dialog.app,
        model: dialog.model,
        haiku_model: dialog.app === 'claude' ? dialog.haiku || undefined : undefined,
        sonnet_model: dialog.app === 'claude' ? dialog.sonnet || undefined : undefined,
        opus_model: dialog.app === 'claude' ? dialog.opus || undefined : undefined,
      })
      setMessage(`正在打开 CC Switch（${dialog.label}）。若没反应，请确认已安装 CC Switch。`)
      setDialog(null)
      window.location.href = result.url
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '生成导入链接失败')
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">API Key</h1>
          <p className="mt-1 text-sm text-mist">创建时必须绑死一个上游账号。后台随时可以再看完整 sk-…</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="line"
            onClick={() => {
              void navigator.clipboard.writeText(`${window.location.origin}/share`)
              setMessage('已复制分享页地址，把链接和 Key 发给对方即可。')
            }}
          >
            复制分享页
          </Button>
          <Button type="button" variant="line" onClick={() => window.open('/share', '_blank', 'noopener')}>
            打开分享页
          </Button>
        </div>
      </div>
      <Card className="grid gap-3 md:grid-cols-3">
        <Field label="备注">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="给同事 A" />
        </Field>
        <Field label="绑定上游">
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value ? Number(event.target.value) : '')}
          >
            <option value="">选择账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.provider})
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-end">
          <Button disabled={!name || !accountId || createMutation.isPending} onClick={() => createMutation.mutate()}>
            生成 Key
          </Button>
        </div>
      </Card>
      {message ? <div className="text-sm text-info">{message}</div> : null}
      <div className="grid gap-3">
        {keys.map((item) => {
          const full = revealed[item.id]
          return (
            <Card key={item.id} className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-lg font-medium">{item.name}</div>
                  <div className="font-mono text-xs text-mist">
                    {item.key_prefix} · {item.account_name} · {item.provider}
                  </div>
                </div>
                <Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{item.status}</Badge>
              </div>
              <div className="text-sm text-mist">最近使用：{formatTime(item.last_used_at)}</div>
              {full ? (
                <div className="flex flex-wrap items-center gap-2 rounded-md bg-ink p-3 font-mono text-xs break-all">
                  {full}
                  <Button variant="line" onClick={() => copy(full)}>
                    复制
                  </Button>
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button variant="line" onClick={() => showKey(item.id)}>
                  显示完整 Key
                </Button>
                <Button type="button" variant="line" onClick={() => loadCcSwitch(item.id)}>
                  导入 CC Switch
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    await api.updateKey(item.id, { status: item.status === 'active' ? 'disabled' : 'active' })
                    queryClient.invalidateQueries({ queryKey: ['keys'] })
                  }}
                >
                  {item.status === 'active' ? '停用' : '启用'}
                </Button>
                <Button
                  variant="danger"
                  onClick={async () => {
                    await api.deleteKey(item.id)
                    queryClient.invalidateQueries({ queryKey: ['keys'] })
                  }}
                >
                  删除
                </Button>
              </div>
              {ccPanel[item.id] ? (
                <div className="space-y-2">
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">选择导入到</div>
                  <div className="flex flex-wrap gap-2">
                    {ccPanel[item.id].targets.map((target) => (
                      <Button
                        key={target.app}
                        type="button"
                        variant="line"
                        onClick={() => openCcTarget(item.id, target, ccPanel[item.id].models)}
                      >
                        {target.label}
                      </Button>
                    ))}
                  </div>
                </div>
              ) : null}
            </Card>
          )
        })}
      </div>
      {dialog ? (
        <Dialog title={`导入到 ${dialog.label}`} onClose={() => setDialog(null)}>
          <div className="space-y-3">
            <p className="text-sm text-mist">模型来自该 Key 绑定的上游账号，请按 {dialog.label} 的角色选好再导入。</p>
            <Field label={dialog.app === 'claude' ? '主模型' : '模型'}>
              <select
                className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
                value={dialog.model}
                onChange={(event) => setDialog({ ...dialog, model: event.target.value })}
              >
                {dialog.models.map((modelName) => (
                  <option key={modelName} value={modelName}>
                    {modelName}
                  </option>
                ))}
              </select>
            </Field>
            {dialog.app === 'claude' ? (
              <>
                <Field label="Haiku 模型（可选）">
                  <select
                    className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
                    value={dialog.haiku}
                    onChange={(event) => setDialog({ ...dialog, haiku: event.target.value })}
                  >
                    <option value="">不设置</option>
                    {dialog.models.map((modelName) => (
                      <option key={modelName} value={modelName}>
                        {modelName}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Sonnet 模型（可选）">
                  <select
                    className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
                    value={dialog.sonnet}
                    onChange={(event) => setDialog({ ...dialog, sonnet: event.target.value })}
                  >
                    <option value="">不设置</option>
                    {dialog.models.map((modelName) => (
                      <option key={modelName} value={modelName}>
                        {modelName}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Opus 模型（可选）">
                  <select
                    className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
                    value={dialog.opus}
                    onChange={(event) => setDialog({ ...dialog, opus: event.target.value })}
                  >
                    <option value="">不设置</option>
                    {dialog.models.map((modelName) => (
                      <option key={modelName} value={modelName}>
                        {modelName}
                      </option>
                    ))}
                  </select>
                </Field>
              </>
            ) : null}
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={() => setDialog(null)}>
                取消
              </Button>
              <Button type="button" onClick={() => confirmDialog()}>
                打开 CC Switch
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}
