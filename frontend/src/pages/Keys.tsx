import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { api, type ApiKeySort, type CcSwitchTarget } from '../lib/api'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

function shareText(shareUrl: string, apiKey: string) {
  return [
    '管理员通过中转台给你下发了 新的api-key：',
    '------',
    `使用链接：${shareUrl}`,
    `api-key：${apiKey}`,
    '------',
    '打开链接后，把api-key粘贴到查询框，即可查看模型和用量，并一键导入客户端，请勿外传。',
  ].join('\n')
}

export function KeysPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const [sort, setSort] = useState<ApiKeySort>('last_used')
  const { data: keys = [] } = useQuery({ queryKey: ['keys', sort], queryFn: () => api.keys(sort) })
  const [name, setName] = useState('')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [accountFilter, setAccountFilter] = useState('')
  const [revealed, setRevealed] = useState<Record<number, string>>({})
  const [ccPanel, setCcPanel] = useState<Record<number, { models: string[]; targets: CcSwitchTarget[] }>>({})
  const [dialog, setDialog] = useState<{
    keyId: number
    app: string
    label: string
    models: string[]
  } | null>(null)

  const createMutation = useMutation({
    mutationFn: () => api.createKey({ name, account_id: Number(accountId) }),
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ['keys'] })
      if (item.key) setRevealed((current) => ({ ...current, [item.id]: item.key as string }))
      setName('')
      notifyOk('已创建，完整 Key 显示在对应卡片上。可把「分享页」和 Key 发给对方自行导入。')
    },
    onError: (error: Error) => notifyBad(error.message),
  })

  const filteredKeys = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return keys.filter((item) => {
      if (statusFilter && item.status !== statusFilter) return false
      if (accountFilter && item.account_id !== Number(accountFilter)) return false
      if (!keyword) return true
      return (
        item.name.toLowerCase().includes(keyword) ||
        item.key_prefix.toLowerCase().includes(keyword) ||
        item.account_name.toLowerCase().includes(keyword) ||
        item.provider.toLowerCase().includes(keyword)
      )
    })
  }, [keys, search, statusFilter, accountFilter])

  async function showKey(id: number) {
    try {
      const item = await api.key(id)
      if (item.key) setRevealed((current) => ({ ...current, [id]: item.key as string }))
    } catch (error) {
      notifyBad(errorMessage(error, '读取完整 Key 失败'))
    }
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text)
    notifyOk('已复制')
  }

  async function shareKey(id: number) {
    try {
      const full = revealed[id] ?? (await api.key(id)).key
      if (!full) {
        notifyBad('无法读取完整 Key')
        return
      }
      setRevealed((current) => ({ ...current, [id]: full }))
      await navigator.clipboard.writeText(shareText(`${window.location.origin}/share`, full))
      notifyOk('已复制分享文案，发给对方即可。')
    } catch (error) {
      notifyBad(errorMessage(error, '复制分享文案失败'))
    }
  }

  async function loadCcSwitch(id: number) {
    try {
      const result = await api.ccSwitch(id)
      setCcPanel((current) => ({ ...current, [id]: { models: result.models, targets: result.targets } }))
      if (result.models.length === 0) {
        notifyBad('绑定账号还没有模型列表，请先到「上游账号」点「获取模型」。')
      }
    } catch (error) {
      notifyBad(errorMessage(error, '无法生成 CC Switch 链接'))
    }
  }

  function openCcTarget(keyId: number, target: CcSwitchTarget, models: string[]) {
    if (!target.needs_dialog) {
      if (target.url) {
        notifyInfo(`正在打开 CC Switch（${target.label}）。`)
        window.location.href = target.url
      }
      return
    }
    if (models.length === 0) {
      notifyBad('绑定账号还没有模型，请先到「上游账号」点「获取模型」。')
      return
    }
    setDialog({
      keyId,
      app: target.app,
      label: target.label,
      models,
    })
  }

  async function confirmDialog(values: CcSwitchValues) {
    if (!dialog) return
    try {
      const result = await api.ccSwitchBuild(dialog.keyId, {
        app: dialog.app,
        model: values.model,
        haiku_model: dialog.app === 'claude' ? values.haiku || undefined : undefined,
        sonnet_model: dialog.app === 'claude' ? values.sonnet || undefined : undefined,
        opus_model: dialog.app === 'claude' ? values.opus || undefined : undefined,
      })
      notifyInfo(`正在打开 CC Switch（${dialog.label}）。若没反应，请确认已安装 CC Switch。`)
      setDialog(null)
      window.location.href = result.url
    } catch (error) {
      notifyBad(errorMessage(error, '生成导入链接失败'))
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">API Key</h1>
          <p className="mt-1 text-sm text-mist">创建时必须绑死一个上游账号。后台随时可以再看完整 sk-…</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="排序">
            <Select
              className="w-44"
              value={sort}
              onChange={(event) => setSort(event.target.value as ApiKeySort)}
            >
              <option value="created_at">按创建时间</option>
              <option value="tokens">按 Token 消耗</option>
              <option value="last_used">按最近使用</option>
            </Select>
          </Field>
          <Button
            type="button"
            variant="line"
            onClick={() => {
              void navigator.clipboard.writeText(`${window.location.origin}/share`)
              notifyOk('已复制自助查询页地址，把链接和 Key 发给对方即可。')
            }}
          >
            复制自助查询页
          </Button>
          <Button type="button" variant="line" onClick={() => window.open('/share', '_blank', 'noopener')}>
            打开自助查询
          </Button>
        </div>
      </div>
      <Card className="grid gap-3 md:grid-cols-3">
        <Field label="备注">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="给同事 A" />
        </Field>
        <Field label="绑定上游">
          <Select
            value={accountId}
            onChange={(event) => setAccountId(event.target.value ? Number(event.target.value) : '')}
          >
            <option value="">选择账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.provider})
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex items-end">
          <Button disabled={!name || !accountId || createMutation.isPending} onClick={() => createMutation.mutate()}>
            生成 Key
          </Button>
        </div>
      </Card>
      <Card className="grid gap-3 md:grid-cols-3">
        <Field label="搜索">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索备注 / Key 前缀 / 账号"
          />
        </Field>
        <Field label="状态">
          <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </Select>
        </Field>
        <Field label="绑定账号">
          <Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}>
            <option value="">全部账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </Select>
        </Field>
      </Card>
      <div className="grid gap-3">
        {filteredKeys.map((item) => {
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
              <div className="grid gap-3 sm:grid-cols-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">今日 Token</div>
                  <div className="mt-1 font-mono text-lg text-signal">{item.today_tokens}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">总 Token</div>
                  <div className="mt-1 font-mono text-lg text-signal">{item.total_tokens}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">最近使用</div>
                  <div className="mt-1 text-sm text-paper">{formatTime(item.last_used_at)}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">创建时间</div>
                  <div className="mt-1 text-sm text-paper">{formatTime(item.created_at)}</div>
                </div>
              </div>
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
                <Button type="button" variant="line" onClick={() => void shareKey(item.id)}>
                  分享
                </Button>
                <Button type="button" variant="line" onClick={() => loadCcSwitch(item.id)}>
                  导入 CC Switch
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await api.updateKey(item.id, { status: item.status === 'active' ? 'disabled' : 'active' })
                      queryClient.invalidateQueries({ queryKey: ['keys'] })
                    } catch (error) {
                      notifyBad(errorMessage(error, '操作失败'))
                    }
                  }}
                >
                  {item.status === 'active' ? '停用' : '启用'}
                </Button>
                <Button
                  variant="danger"
                  onClick={async () => {
                    try {
                      await api.deleteKey(item.id)
                      queryClient.invalidateQueries({ queryKey: ['keys'] })
                      queryClient.invalidateQueries({ queryKey: ['logs'] })
                      notifyOk('已删除')
                    } catch (error) {
                      notifyBad(errorMessage(error, '删除失败'))
                    }
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
        <CcSwitchDialog
          label={dialog.label}
          models={dialog.models}
          isClaude={dialog.app === 'claude'}
          initial={{ model: dialog.models[0] ?? '', haiku: '', sonnet: '', opus: '' }}
          onConfirm={(values) => void confirmDialog(values)}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  )
}
