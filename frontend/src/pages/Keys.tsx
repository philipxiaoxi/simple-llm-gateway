import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Children, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type ApiKeySort, type CcSwitchTarget, type GatewayAgent } from '../lib/api'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime, formatTokenCount, RISK_LEVELS } from '../lib/utils'

function MoreMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [upward, setUpward] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDocumentClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocumentClick)
    return () => document.removeEventListener('mousedown', onDocumentClick)
  }, [open])

  function toggle() {
    const next = !open
    setOpen(next)
    if (next) {
      // 打开时判断：下方空间不足则向上弹出
      const rect = containerRef.current?.getBoundingClientRect()
      if (rect) {
        const itemCount = Children.count(children)
        const estimatedHeight = itemCount * 36 + 16
        setUpward(rect.bottom + estimatedHeight > window.innerHeight)
      }
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <Button type="button" variant="line" onClick={toggle}>
        更多
      </Button>
      {open ? (
        <div
          className={cn(
            'absolute right-0 z-20 min-w-40 rounded-md border border-line bg-panel-2 p-1 shadow-[0_10px_30px_rgba(0,0,0,0.4)]',
            upward ? 'bottom-full mb-1' : 'top-full mt-1',
          )}
        >
          {children}
        </div>
      ) : null}
    </div>
  )
}

function MenuItem({
  children,
  danger,
  onClick,
}: {
  children: ReactNode
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'block w-full rounded-md px-3 py-2 text-left text-sm transition',
        danger ? 'text-danger hover:bg-danger/10' : 'text-paper hover:bg-white/5',
      )}
    >
      {children}
    </button>
  )
}

function KeyEditDialog({
  keyId,
  initialName,
  initialAccountId,
  accounts,
  onClose,
  onSaved,
}: {
  keyId: number
  initialName: string
  initialAccountId: number
  accounts: { id: number; name: string; provider: string; source: 'upstream' | 'agent' }[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(initialName)
  const [accountId, setAccountId] = useState<number | ''>(initialAccountId)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function save() {
    const trimmed = name.trim()
    if (!trimmed) {
      setError('请填写备注')
      return
    }
    if (!accountId) {
      setError('请选择上游账号')
      return
    }
    setPending(true)
    setError('')
    try {
      await api.updateKey(keyId, { name: trimmed, account_id: Number(accountId) })
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="编辑 API Key" onClose={onClose}>
      <div className="grid gap-3">
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
                {accountSourceLabel(account.source)} {account.name} ({account.provider})
              </option>
            ))}
          </Select>
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void save()}>
            保存
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

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

function accountSourceLabel(source: 'upstream' | 'agent') {
  return source === 'agent' ? '[网关]' : '[上游]'
}

export function KeysPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: agentData } = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const [sort, setSort] = useState<ApiKeySort>('last_used')
  const { data: keys = [] } = useQuery({ queryKey: ['keys', sort], queryFn: () => api.keys(sort) })
  const [name, setName] = useState('')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [accountFilter, setAccountFilter] = useState('')
  const [revealed, setRevealed] = useState<Record<number, string>>({})
  const [ccPanel, setCcPanel] = useState<
    Record<number, { models: string[]; targets: CcSwitchTarget[]; vscode: Record<string, unknown> }>
  >({})
  const [dialog, setDialog] = useState<{
    keyId: number
    app: string
    label: string
    models: string[]
  } | null>(null)
  const [editDialog, setEditDialog] = useState<{ keyId: number; name: string; accountId: number } | null>(null)

  const createMutation = useMutation({
    mutationFn: () => api.createKey({ name, account_id: Number(accountId) }),
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ['keys'] })
      if (item.key) {
        setRevealed((current) => ({ ...current, [item.id]: item.key as string }))
        void navigator.clipboard.writeText(item.key).then(
          () => notifyOk('已创建，完整 Key 已复制。可把「分享页」和 Key 发给对方自行导入。'),
          () => notifyOk('已创建。请点「复制 Key」再发给对方。'),
        )
      } else {
        notifyOk('已创建。请点「复制 Key」再发给对方。')
      }
      setName('')
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

  const availableAccounts = useMemo(() => {
    const onlineRouteIds = new Set(
      (agentData?.items ?? [])
        .filter((agent: GatewayAgent) => agent.status === 'online')
        .flatMap((agent: GatewayAgent) => agent.routes.map((route) => route.id)),
    )
    return accounts.filter((account) => account.source !== 'agent' || onlineRouteIds.has(account.agent_route_id ?? ''))
  }, [accounts, agentData])

  async function copyKey(id: number) {
    try {
      const full = revealed[id] ?? (await api.key(id)).key
      if (!full) {
        notifyBad('无法读取完整 Key')
        return
      }
      setRevealed((current) => ({ ...current, [id]: full }))
      await navigator.clipboard.writeText(full)
      notifyOk('已复制')
    } catch (error) {
      notifyBad(errorMessage(error, '复制 Key 失败'))
    }
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

  async function loadImport(id: number) {
    try {
      const result = await api.ccSwitch(id)
      setCcPanel((current) => ({
        ...current,
        [id]: { models: result.models, targets: result.targets, vscode: result.vscode },
      }))
      if (result.models.length === 0) {
        notifyBad('绑定账号还没有模型列表，请先到「上游账号」点「获取模型」。')
      }
    } catch (error) {
      notifyBad(errorMessage(error, '无法生成导入配置'))
    }
  }

  async function importToVscode(id: number) {
    const panel = ccPanel[id]
    if (!panel) return
    if (panel.models.length === 0) {
      notifyBad('绑定账号还没有模型，请先到「上游账号」点「获取模型」。')
      return
    }
    try {
      const text = JSON.stringify(panel.vscode, null, 2)
      await navigator.clipboard.writeText(text)
      notifyOk('VSCode 配置已复制，粘贴到 chatLanguageModels.json 即可。')
    } catch (error) {
      notifyBad(errorMessage(error, '复制 VSCode 配置失败'))
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
          <p className="mt-1 text-sm text-mist">创建时绑定一个上游账号或网关 Agent 账号。需要时直接复制完整 Key。</p>
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
            {availableAccounts.map((account) => (
              <option key={account.id} value={account.id}>
                {accountSourceLabel(account.source)} {account.name} ({account.provider})
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
                {accountSourceLabel(account.source)} {account.name}
              </option>
            ))}
          </Select>
        </Field>
      </Card>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {filteredKeys.map((item) => {
          return (
            <Card key={item.id} className="flex flex-col space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-lg font-medium">{item.name}</div>
                    <Badge
                      tone={RISK_LEVELS[item.risk_level]?.tone ?? 'mist'}
                      title={`上游账号风险：${RISK_LEVELS[item.risk_level]?.hint ?? ''}`}
                    >
                      {RISK_LEVELS[item.risk_level]?.label ?? item.risk_level}
                    </Badge>
                    <Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{item.status}</Badge>
                  </div>
                  <div className="mt-1 font-mono text-xs text-mist">
                    {item.key_prefix} · {accountSourceLabel(item.account_source)} {item.account_name} · {item.provider}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">今日 Token</div>
                  <div className="mt-1 font-mono text-xl text-signal">{formatTokenCount(item.today_tokens)}</div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">总 Token</div>
                  <div className="mt-1 font-mono text-xl text-signal">{formatTokenCount(item.total_tokens)}</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist">
                <span>最近使用：{formatTime(item.last_used_at)}</span>
                <span>创建时间：{formatTime(item.created_at)}</span>
              </div>
              <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                <Button variant="line" onClick={() => void copyKey(item.id)}>
                  复制 Key
                </Button>
                <Button type="button" variant="line" onClick={() => void shareKey(item.id)}>
                  分享
                </Button>
                <Button type="button" variant="line" onClick={() => loadImport(item.id)}>
                  导入
                </Button>
                <MoreMenu>
                  <MenuItem
                    onClick={() => setEditDialog({ keyId: item.id, name: item.name, accountId: item.account_id })}
                  >
                    编辑
                  </MenuItem>
                  <MenuItem
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
                  </MenuItem>
                  <MenuItem
                    danger
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
                  </MenuItem>
                </MoreMenu>
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
                    <Button type="button" variant="line" onClick={() => void importToVscode(item.id)}>
                      VSCode
                    </Button>
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
      {editDialog ? (
        <KeyEditDialog
          keyId={editDialog.keyId}
          initialName={editDialog.name}
          initialAccountId={editDialog.accountId}
          accounts={accounts}
          onClose={() => setEditDialog(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ['keys'] })
            setEditDialog(null)
            notifyOk('已更新')
          }}
        />
      ) : null}
    </div>
  )
}
