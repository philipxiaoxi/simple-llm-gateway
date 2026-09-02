import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Children, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { ModelAliasList } from '../components/ModelAliasList'
import { VscodeImportDialog } from '../components/VscodeImportDialog'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type Account, type ApiKeyItem, type ApiKeySort, type CcSwitchTarget, type GatewayAgent, type ShareAlias } from '../lib/api'
import { openShareWithApiKey } from '../lib/shareTransfer'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { ALIAS_INPUT_PATTERN, cn, errorMessage, formatTime, formatTokenCount, RISK_LEVELS } from '../lib/utils'

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
      const rect = containerRef.current?.getBoundingClientRect()
      if (rect) {
        const itemCount = Children.count(children)
        const estimatedHeight = itemCount * 36 + 16
        setUpward(rect.bottom + estimatedHeight > window.innerHeight)
      }
    }
  }

  return (
    <div ref={containerRef} className="relative w-full md:w-auto">
      <Button type="button" variant="line" className="w-full md:w-auto" onClick={toggle}>
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
        danger ? 'bg-danger/15 text-danger hover:bg-danger/25' : 'text-paper hover:bg-white/5',
      )}
    >
      {children}
    </button>
  )
}

function accountSourceLabel(source: 'upstream' | 'agent') {
  return source === 'agent' ? '[网关]' : '[上游]'
}

function keyBoundIds(item: ApiKeyItem): number[] {
  if (item.account_ids?.length) return item.account_ids
  if (item.accounts?.length) return item.accounts.map((account) => account.id)
  return item.account_id != null ? [item.account_id] : []
}

function BoundAccountList({
  accounts,
  selectedIds,
  onChange,
}: {
  accounts: Account[]
  selectedIds: number[]
  onChange: (ids: number[]) => void
}) {
  const [addId, setAddId] = useState<number | ''>('')
  const remaining = accounts.filter((account) => !selectedIds.includes(account.id))

  function move(index: number, delta: number) {
    const target = index + delta
    if (target < 0 || target >= selectedIds.length) return
    const next = [...selectedIds]
    const current = next[index]
    next[index] = next[target]
    next[target] = current
    onChange(next)
    notifyInfo('调整顺序后，排第一的账号优先使用')
  }

  function remove(index: number) {
    if (selectedIds.length <= 1) return
    onChange(selectedIds.filter((_, itemIndex) => itemIndex !== index))
  }

  function add() {
    if (!addId || selectedIds.includes(addId)) return
    onChange([...selectedIds, addId])
    setAddId('')
  }

  return (
    <div className="grid gap-2">
      {selectedIds.map((accountId, index) => {
        const account = accounts.find((item) => item.id === accountId)
        const label = account
          ? `${accountSourceLabel(account.source)} ${account.name} (${account.provider})`
          : `账号 #${accountId}`
        return (
          <div
            key={accountId}
            className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-ink/40 px-3 py-2"
          >
            <div className="min-w-0 flex-1 text-sm">
              {label}
              {index === 0 ? <span className="ml-2 text-xs text-signal">优先</span> : null}
            </div>
            <Button type="button" variant="line" disabled={index === 0} onClick={() => move(index, -1)}>
              上移
            </Button>
            <Button
              type="button"
              variant="line"
              disabled={index === selectedIds.length - 1}
              onClick={() => move(index, 1)}
            >
              下移
            </Button>
            <Button
              type="button"
              variant="line"
              className="text-danger hover:border-danger hover:bg-danger/15"
              disabled={selectedIds.length <= 1}
              onClick={() => remove(index)}
            >
              移除
            </Button>
          </div>
        )
      })}
      {remaining.length ? (
        <div className="flex items-end gap-2">
          <Select
            className="min-w-0 flex-1"
            value={addId}
            onChange={(event) => setAddId(event.target.value ? Number(event.target.value) : '')}
          >
            <option value="">添加账号</option>
            {remaining.map((account) => (
              <option key={account.id} value={account.id}>
                {accountSourceLabel(account.source)} {account.name} ({account.provider})
              </option>
            ))}
          </Select>
          <Button type="button" variant="line" className="shrink-0" disabled={!addId} onClick={add}>
            添加
          </Button>
        </div>
      ) : null}
    </div>
  )
}

function KeyEditDialog({
  keyId,
  initialName,
  initialAccountIds,
  accounts,
  onClose,
  onSaved,
}: {
  keyId: number
  initialName: string
  initialAccountIds: number[]
  accounts: Account[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(initialName)
  const [accountIds, setAccountIds] = useState<number[]>(initialAccountIds)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function save() {
    const trimmed = name.trim()
    if (!trimmed) {
      setError('请填写备注')
      return
    }
    if (!accountIds.length) {
      setError('请至少绑定一个上游账号')
      return
    }
    setPending(true)
    setError('')
    try {
      await api.updateKey(keyId, { name: trimmed, account_ids: accountIds })
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
          <BoundAccountList accounts={accounts} selectedIds={accountIds} onChange={setAccountIds} />
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

function KeyAliasesDialog({ keyId, keyName, onClose }: { keyId: number; keyName: string; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [aliasDraft, setAliasDraft] = useState('')
  const [modelDraft, setModelDraft] = useState('')
  const [pending, setPending] = useState(false)
  const { data } = useQuery({ queryKey: ['key-aliases', keyId], queryFn: () => api.keyAliases(keyId) })
  const aliases: ShareAlias[] = data?.aliases ?? []
  const models: string[] = data?.models ?? []

  async function mutate(action: () => Promise<unknown>, successText: string) {
    if (pending) return
    setPending(true)
    try {
      await action()
      await queryClient.invalidateQueries({ queryKey: ['key-aliases', keyId] })
      notifyOk(successText)
    } catch (error) {
      notifyBad(errorMessage(error, '操作失败'))
    } finally {
      setPending(false)
    }
  }

  function submitAlias() {
    const alias = aliasDraft.trim()
    const model = modelDraft || models[0]
    if (!ALIAS_INPUT_PATTERN.test(alias)) {
      notifyBad('别名仅允许字母、数字和 . _ / -，以字母或数字开头，最长 64 位。')
      return
    }
    if (aliases.some((item) => item.alias === alias)) {
      notifyBad(`别名 ${alias} 已存在，可直接在列表里切换它对应的模型。`)
      return
    }
    if (!model) {
      notifyBad('请选择别名对应的模型。')
      return
    }
    void mutate(() => api.keyAliasSave(keyId, { alias, model }), `已创建别名 ${alias}`)
    setAliasDraft('')
  }

  return (
    <Dialog title={`模型别名 · ${keyName}`} onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-sm text-mist">
          自定义名字映射到该 Key 的任意可用模型。客户端模型名填别名，切换背后的模型后客户端无需改配置，自助查询页同步生效。
        </p>
        {aliases.length > 0 ? (
          <ModelAliasList
            aliases={aliases}
            models={models}
            busy={pending}
            onSwitch={(alias, model) =>
              void mutate(() => api.keyAliasSave(keyId, { alias, model }), `别名 ${alias} 已切换到 ${model}`)
            }
            onRename={(oldAlias, newAlias) =>
              void mutate(
                () => api.keyAliasRename(keyId, { old_alias: oldAlias, new_alias: newAlias }),
                `别名 ${oldAlias} 已重命名为 ${newAlias}`,
              )
            }
            onDelete={(alias) => void mutate(() => api.keyAliasDelete(keyId, alias), `已删除别名 ${alias}`)}
          />
        ) : (
          <div className="rounded-lg border border-dashed border-line bg-ink/40 px-3 py-4 text-sm text-mist">
            还没有别名。添加一个，比如 fast 指向常用模型。
          </div>
        )}
        {models.length > 0 ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-36 flex-1 sm:max-w-52">
              <Field label="别名">
                <Input
                  value={aliasDraft}
                  onChange={(event) => setAliasDraft(event.target.value)}
                  placeholder="例如 fast"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      submitAlias()
                    }
                  }}
                />
              </Field>
            </div>
            <div className="min-w-36 flex-1 sm:max-w-64">
              <Field label="指向模型">
                <Select value={modelDraft || models[0]} onChange={(event) => setModelDraft(event.target.value)}>
                  {models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            <Button type="button" disabled={pending} onClick={submitAlias}>
              添加别名
            </Button>
          </div>
        ) : (
          <div className="text-sm text-warn">该 Key 还没有可用模型，请先到「上游账号」点「获取模型」。</div>
        )}
        <div className="flex justify-end">
          <Button type="button" variant="ghost" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function shareText(shareUrl: string, apiKey: string) {
  return [
    '管理员通过AI一体化服务平台给你下发了 新的api-key：',
    '------',
    `使用链接：${shareUrl}`,
    `api-key：${apiKey}`,
    '------',
    '打开链接后，把api-key粘贴到查询框，即可查看模型和用量，并一键导入客户端，请勿外传。',
  ].join('\n')
}

export function KeysPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: agentData } = useQuery({ queryKey: ['agents'], queryFn: api.agents })
  const [sort, setSort] = useState<ApiKeySort>('last_used')
  const { data: keys = [] } = useQuery({ queryKey: ['keys', sort], queryFn: () => api.keys(sort) })
  const [name, setName] = useState('')
  const [accountIds, setAccountIds] = useState<number[]>([])
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [accountFilter, setAccountFilter] = useState('')
  const [revealed, setRevealed] = useState<Record<number, string>>({})
  const [createDialog, setCreateDialog] = useState(false)
  const [ccPanel, setCcPanel] = useState<
    Record<number, { models: string[]; targets: CcSwitchTarget[]; vscode: Record<string, unknown> }>
  >({})
  const [dialog, setDialog] = useState<{
    keyId: number
    app: string
    label: string
    models: string[]
  } | null>(null)
  const [vscodeDialog, setVscodeDialog] = useState<Record<string, unknown> | null>(null)
  const [boundAccountsDialog, setBoundAccountsDialog] = useState<{
    keyName: string
    accounts: NonNullable<ApiKeyItem['accounts']>
  } | null>(null)
  const [editDialog, setEditDialog] = useState<{ keyId: number; name: string; accountIds: number[] } | null>(null)
  const [aliasesDialog, setAliasesDialog] = useState<{ keyId: number; keyName: string } | null>(null)

  const createMutation = useMutation({
    mutationFn: () => api.createKey({ name, account_ids: accountIds }),
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
      setAccountIds([])
      setCreateDialog(false)
    },
    onError: (error: Error) => notifyBad(error.message),
  })

  const filteredKeys = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    const filterId = accountFilter ? Number(accountFilter) : null
    return keys.filter((item) => {
      if (statusFilter && item.status !== statusFilter) return false
      if (filterId != null) {
        const bound = keyBoundIds(item)
        const matches =
          bound.includes(filterId) ||
          item.accounts?.some((account) => account.id === filterId) ||
          item.account_id === filterId
        if (!matches) return false
      }
      if (!keyword) return true
      const boundNames = (item.accounts ?? []).map((account) => account.name.toLowerCase())
      const boundProviders = (item.accounts ?? []).map((account) => account.provider.toLowerCase())
      return (
        item.name.toLowerCase().includes(keyword) ||
        item.key_prefix.toLowerCase().includes(keyword) ||
        item.account_name.toLowerCase().includes(keyword) ||
        item.provider.toLowerCase().includes(keyword) ||
        boundNames.some((value) => value.includes(keyword)) ||
        boundProviders.some((value) => value.includes(keyword))
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

  async function queryKey(id: number) {
    try {
      const full = revealed[id] ?? (await api.key(id)).key
      if (!full) {
        notifyBad('无法读取完整 Key')
        return
      }
      setRevealed((current) => ({ ...current, [id]: full }))
      if (!openShareWithApiKey(full)) {
        notifyBad('无法打开新标签页，请允许浏览器打开弹窗后重试')
      }
    } catch (error) {
      notifyBad(errorMessage(error, '查询 Key 失败'))
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

  function importToVscode(id: number) {
    const panel = ccPanel[id]
    if (!panel) return
    if (panel.models.length === 0) {
      notifyBad('绑定账号还没有模型，请先到「上游账号」点「获取模型」。')
      return
    }
    setVscodeDialog(panel.vscode)
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
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold">API Key</h1>
            <p className="mt-1 text-sm text-mist">
              创建时绑定一个或多个上游账号、网关代理账号。排第一的账号优先使用。需要时直接复制完整 Key。
            </p>
          </div>
          <Button type="button" className="shrink-0 lg:hidden" onClick={() => setCreateDialog(true)}>
            生成 Key
          </Button>
        </div>
        <div className="grid grid-cols-2 items-end gap-2 lg:flex lg:shrink-0 lg:flex-nowrap">
          <div className="col-span-2 min-w-0 lg:w-44">
            <Field label="排序">
              <Select
                className="w-full"
                value={sort}
                onChange={(event) => setSort(event.target.value as ApiKeySort)}
              >
                <option value="created_at">按创建时间</option>
                <option value="tokens">按 Token 消耗</option>
                <option value="last_used">按最近使用</option>
              </Select>
            </Field>
          </div>
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            onClick={() => {
              void navigator.clipboard.writeText(`${window.location.origin}/share`)
              notifyOk('已复制自助查询页地址，把链接和 Key 发给对方即可。')
            }}
          >
            复制查询页
          </Button>
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            onClick={() => window.open('/share', '_blank', 'noopener')}
          >
            打开查询页
          </Button>
          <Button type="button" className="hidden lg:inline-flex" onClick={() => setCreateDialog(true)}>
            生成 Key
          </Button>
        </div>
      </div>
      <Card className="grid grid-cols-2 gap-3 md:grid-cols-3">
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
        <div className="col-span-2 md:col-span-1">
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
        </div>
      </Card>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {filteredKeys.map((item) => {
          const boundAccounts = item.accounts?.length
            ? item.accounts
            : [
                {
                  id: item.account_id ?? 0,
                  name: item.account_name,
                  provider: item.provider,
                  source: item.account_source,
                  status: item.status,
                  risk_level: item.risk_level,
                },
              ]
          return (
            <Card key={item.id} className="flex flex-col space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-lg font-medium">{item.name}</div>
                    {boundAccounts.length > 1 ? (
                      <Badge tone="info">多账号风险</Badge>
                    ) : (
                      <Badge
                        tone={RISK_LEVELS[item.risk_level]?.tone ?? 'mist'}
                        title={`上游账号风险：${RISK_LEVELS[item.risk_level]?.hint ?? ''}`}
                      >
                        {RISK_LEVELS[item.risk_level]?.label ?? item.risk_level}
                      </Badge>
                    )}
                    <Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{item.status}</Badge>
                  </div>
                  <div className="mt-1 font-mono text-xs text-mist">{item.key_prefix}</div>
                  <div className="mt-2 grid gap-1 text-xs text-mist">
                    {boundAccounts.length > 1 ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <div>
                          {accountSourceLabel(boundAccounts[0].source)} {boundAccounts[0].name} · {boundAccounts[0].provider}
                        </div>
                        <button
                          type="button"
                          className="text-left text-signal underline decoration-signal/50 underline-offset-2 hover:text-paper"
                          onClick={() => setBoundAccountsDialog({ keyName: item.name, accounts: boundAccounts })}
                        >
                          已绑定 {boundAccounts.length} 个账号
                        </button>
                      </div>
                    ) : boundAccounts.length === 1 ? (
                      <div>
                        {accountSourceLabel(boundAccounts[0].source)} {boundAccounts[0].name} · {boundAccounts[0].provider}
                      </div>
                    ) : null}
                    {boundAccounts.length > 1 ? (
                      <div className="text-xs text-mist">风险请查看已绑定账号明细</div>
                    ) : null}
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
              <div className="grid grid-cols-2 gap-2 border-t border-line pt-3 md:flex md:flex-wrap md:items-center">
                <Button type="button" variant="line" className="w-full md:w-auto" onClick={() => void queryKey(item.id)}>
                  查询
                </Button>
                <Button variant="line" className="w-full md:w-auto" onClick={() => void copyKey(item.id)}>
                  复制 Key
                </Button>
                <Button type="button" variant="line" className="w-full md:w-auto" onClick={() => void shareKey(item.id)}>
                  分享
                </Button>
                <Button type="button" variant="line" className="w-full md:w-auto" onClick={() => loadImport(item.id)}>
                  导入
                </Button>
                <div className="col-span-2 md:col-auto md:w-auto">
                <MoreMenu>
                  <MenuItem
                    onClick={() => setEditDialog({ keyId: item.id, name: item.name, accountIds: keyBoundIds(item) })}
                  >
                    编辑
                  </MenuItem>
                  <MenuItem onClick={() => setAliasesDialog({ keyId: item.id, keyName: item.name })}>
                    模型别名
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
                    <Button type="button" variant="line" onClick={() => importToVscode(item.id)}>
                      VSCode
                    </Button>
                  </div>
                </div>
              ) : null}
            </Card>
          )
        })}
      </div>
      {vscodeDialog ? <VscodeImportDialog config={vscodeDialog} onClose={() => setVscodeDialog(null)} /> : null}
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
      {createDialog ? (
        <Dialog title="生成 API Key" onClose={() => setCreateDialog(false)}>
          <div className="grid gap-3">
            <Field label="备注">
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="给同事 A" />
            </Field>
            <Field label="绑定上游">
              <BoundAccountList accounts={availableAccounts} selectedIds={accountIds} onChange={setAccountIds} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setCreateDialog(false)}>
                取消
              </Button>
              <Button disabled={!name || !accountIds.length || createMutation.isPending} onClick={() => createMutation.mutate()}>
                生成 Key
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {boundAccountsDialog ? (
        <Dialog
          title={`绑定账号 · ${boundAccountsDialog.keyName}`}
          onClose={() => setBoundAccountsDialog(null)}
        >
          <div className="grid gap-2">
            {boundAccountsDialog.accounts.map((account, index) => {
              const gatewayAccount = accounts.find((item) => item.id === account.id)
              const onlineRouteIds = new Set(
                (agentData?.items ?? [])
                  .filter((agent: GatewayAgent) => agent.status === 'online')
                  .flatMap((agent: GatewayAgent) => agent.routes.map((route) => route.id)),
              )
              const isOnline =
                account.source !== 'agent' ||
                onlineRouteIds.has(gatewayAccount?.agent_route_id ?? '')
              return (
                <div
                  key={account.id}
                  className="flex items-center justify-between gap-3 rounded-md border border-line bg-ink/40 px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    {accountSourceLabel(account.source)} {account.name} · {account.provider}
                    {index === 0 ? <span className="ml-1 text-signal">优先</span> : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-xs">
                    <Badge tone={account.status === 'active' ? 'ok' : 'mist'}>
                      {account.status === 'active' ? '启用' : '停用'}
                    </Badge>
                    <Badge tone={RISK_LEVELS[account.risk_level]?.tone ?? 'mist'}>
                      {RISK_LEVELS[account.risk_level]?.label ?? account.risk_level}
                    </Badge>
                    {account.source === 'agent' ? (
                      <Badge tone={isOnline ? 'ok' : 'bad'}>{isOnline ? '在线' : '离线'}</Badge>
                    ) : null}
                  </div>
                </div>
              )
            })}
          </div>
        </Dialog>
      ) : null}
      {editDialog ? (
        <KeyEditDialog
          keyId={editDialog.keyId}
          initialName={editDialog.name}
          initialAccountIds={editDialog.accountIds}
          accounts={accounts.filter(
            (account) => availableAccounts.includes(account) || editDialog.accountIds.includes(account.id),
          )}
          onClose={() => setEditDialog(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ['keys'] })
            setEditDialog(null)
            notifyOk('已更新')
          }}
        />
      ) : null}
      {aliasesDialog ? (
        <KeyAliasesDialog
          keyId={aliasesDialog.keyId}
          keyName={aliasesDialog.keyName}
          onClose={() => setAliasesDialog(null)}
        />
      ) : null}
    </div>
  )
}
