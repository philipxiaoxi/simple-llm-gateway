import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { ModelPickDialog } from '../components/ModelPickDialog'
import { VscodeImportDialog } from '../components/VscodeImportDialog'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { accountColor, shareModelEntries } from '../lib/accountModels'
import { api, type CcSwitchTarget, type ModelCaps, type ShareAlias, type ShareLookup, type ShareModelEntry } from '../lib/api'
import { listenForShareApiKey } from '../lib/shareTransfer'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { MIN_KEY_LENGTH, ALIAS_INPUT_PATTERN, cn, errorMessage, formatTokenCount, modelCapsHint } from '../lib/utils'

const RISK_META: Record<string, { label: string; hint: string; className: string }> = {
  low: { label: '低风险', hint: '官方模型或数据泄露可能性较低', className: 'border-signal/30 bg-signal/10 text-signal' },
  medium: {
    label: '中风险',
    hint: '非官方，可能是中转站或内部部署的模型',
    className: 'border-warn/30 bg-warn/10 text-warn',
  },
  high: {
    label: '高风险',
    hint: '非官方的低价或廉价站点模型，可能存在信息收集',
    className: 'border-danger/30 bg-danger/10 text-danger',
  },
}

function accountSourceLabel(source: 'upstream' | 'agent') {
  return source === 'agent' ? '[网关]' : '[上游]'
}

function accountStatusLabel(account: ShareLookup['accounts'][number]) {
  if (account.source === 'agent') return account.status === 'online' ? '在线' : '离线'
  return account.status === 'active' ? '已启用' : '未启用'
}

function isAccountAvailable(account: ShareLookup['accounts'][number]) {
  return account.source === 'agent' ? account.status === 'online' : account.status === 'active'
}

function BoundAccountCard({
  account,
  index,
}: {
  account: ShareLookup['accounts'][number]
  index: number
}) {
  const risk = RISK_META[account.risk_level]
  const prefix = account.model_prefix ? account.model_prefix : '自动生成'
  const meta = [accountStatusLabel(account), index === 0 ? '优先使用' : null, `前缀 ${prefix}`]
    .filter(Boolean)
    .join(' · ')
  return (
    <li className="px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="w-4 shrink-0 text-center font-mono text-xs text-mist" aria-hidden="true">
          {index + 1}
        </span>
        <span className="shrink-0 text-xs text-mist">{accountSourceLabel(account.source)}</span>
        <span className="min-w-0 truncate text-sm font-medium text-paper" title={account.name}>
          {account.name}
        </span>
        <span
          title={risk?.hint}
          className={cn(
            'ml-auto inline-flex shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium',
            risk?.className ?? 'border-line bg-ink/40 text-mist',
          )}
        >
          {risk?.label ?? account.risk_level}
        </span>
      </div>
      <div className="mt-0.5 truncate pl-6 text-xs text-mist" title={meta}>
        {meta}
      </div>
    </li>
  )
}

function modelCapsOf(lookup: ShareLookup, modelName: string) {
  return lookup.model_caps?.find((item) => item.id === modelName)
}

function modelHint(caps?: ModelCaps) {
  return modelCapsHint(caps)
}

function ModelAliasManager({
  rawKey,
  models,
  aliases,
  onChange,
}: {
  rawKey: string
  models: string[]
  aliases: ShareAlias[]
  onChange: (aliases: ShareAlias[]) => void
}) {
  const [aliasDraft, setAliasDraft] = useState('')
  const [aliasModelDraft, setAliasModelDraft] = useState(models[0] ?? '')
  const [busy, setBusy] = useState(false)

  async function mutate(action: () => Promise<{ aliases: ShareAlias[] }>, successText: string) {
    if (busy) return
    setBusy(true)
    try {
      const result = await action()
      onChange(result.aliases)
      notifyOk(successText)
    } catch (item) {
      notifyBad(errorMessage(item, '操作失败'))
    } finally {
      setBusy(false)
    }
  }

  function submitAlias() {
    const alias = aliasDraft.trim()
    if (!ALIAS_INPUT_PATTERN.test(alias)) {
      notifyBad('别名仅允许字母、数字和 . _ / -，以字母或数字开头，最长 64 位。')
      return
    }
    if (aliases.some((item) => item.alias === alias)) {
      notifyBad(`别名 ${alias} 已存在，可直接在列表里切换它对应的模型。`)
      return
    }
    const model = aliasModelDraft || models[0]
    if (!model) {
      notifyBad('请选择别名对应的模型。')
      return
    }
    void mutate(() => api.shareAliasSave({ api_key: rawKey, alias, model }), `已创建别名 ${alias}`)
    setAliasDraft('')
  }

  return (
    <Card className="space-y-3">
      <div>
        <h2 className="text-lg font-medium">模型别名</h2>
        <p className="mt-1 text-sm text-mist">
          自定义名字映射到任意可用模型。客户端模型名填别名，随时在这里切换它背后的模型，客户端不用改配置。
        </p>
      </div>
      {aliases.length > 0 ? (
        <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-ink/40">
          {aliases.map((item) => {
            const targetMissing = !models.includes(item.model)
            return (
              <li key={item.alias} className="flex flex-wrap items-center gap-2 px-3 py-2">
                <span className="font-mono text-sm text-paper" title={item.alias}>
                  {item.alias}
                </span>
                <span className="shrink-0 text-xs text-mist">→</span>
                <Select
                  className={cn('min-w-0 flex-1 md:w-auto md:flex-none md:min-w-56', targetMissing && 'border-warn/60 text-warn')}
                  value={targetMissing ? '' : item.model}
                  disabled={busy}
                  onChange={(event) =>
                    void mutate(
                      () => api.shareAliasSave({ api_key: rawKey, alias: item.alias, model: event.target.value }),
                      `别名 ${item.alias} 已切换到 ${event.target.value}`,
                    )
                  }
                >
                  {targetMissing ? <option value="">模型已移除，请重新选择</option> : null}
                  {models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </Select>
                <Button
                  type="button"
                  variant="danger"
                  className="shrink-0 px-2"
                  disabled={busy}
                  title={`删除别名 ${item.alias}`}
                  onClick={() => void mutate(() => api.shareAliasDelete({ api_key: rawKey, alias: item.alias }), `已删除别名 ${item.alias}`)}
                >
                  <Trash2 size={15} />
                  删除
                </Button>
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-line bg-ink/40 px-3 py-4 text-sm text-mist">
          还没有别名。添加一个，比如 fast 指向常用模型。
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-40 flex-1 sm:max-w-56">
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
        <div className="min-w-40 flex-1 sm:max-w-72">
          <Field label="指向模型">
            <Select value={aliasModelDraft} onChange={(event) => setAliasModelDraft(event.target.value)}>
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Button type="button" disabled={busy} onClick={submitAlias}>
          <Plus size={15} />
          添加别名
        </Button>
      </div>
    </Card>
  )
}

function AccountModelGroups({
  lookup,
  onModelClick,
}: {
  lookup: ShareLookup
  onModelClick?: (modelName: string) => void
}) {
  const entries = shareModelEntries(lookup)
  const groups = entries.reduce<Array<{ account: ShareModelEntry; models: ShareModelEntry[] }>>((items, entry) => {
    const group = items.find((item) => item.account.account_id === entry.account_id)
    if (group) group.models.push(entry)
    else items.push({ account: entry, models: [entry] })
    return items
  }, [])

  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2">
      {groups.map(({ account, models }) => {
        const color = accountColor(account.account_index)
        return (
          <section
            key={`${account.account_source}-${account.account_id}`}
            className={cn('min-w-0 rounded-lg border border-l-2 p-3', color.border, color.tint)}
          >
            <div className="flex min-w-0 items-center gap-2">
              <span className={cn('size-2 shrink-0 rounded-full', color.dot)} aria-hidden="true" />
              <span className="min-w-0 truncate text-sm font-medium" title={account.account_name}>
                {account.account_name}
              </span>
              <span className="shrink-0 text-[10px] text-mist">{accountSourceLabel(account.account_source)}</span>
              <span className={cn('ml-auto shrink-0 font-mono text-[10px]', color.text)}>优先 {account.account_index + 1}</span>
            </div>
            <div className="mt-1 text-[11px] text-mist">{models.length} 个模型</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {models.map((model) => {
                const title = [model.id, model.raw_id !== model.id ? `原始模型 ${model.raw_id}` : null, modelHint(modelCapsOf(lookup, model.id))]
                  .filter(Boolean)
                  .join(' · ')
                const className = cn('max-w-full rounded-md border px-2 py-1 font-mono text-xs break-all', color.border, color.tint, color.text)
                return onModelClick ? (
                  <button
                    key={model.id}
                    type="button"
                    onClick={() => onModelClick(model.id)}
                    className={cn(className, 'transition', color.hover)}
                    title={title}
                  >
                    {model.id}
                  </button>
                ) : (
                  <span key={model.id} className={className} title={title}>
                    {model.id}
                  </span>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function modelDetailLine(modelName: string, lookup: ShareLookup) {
  const caps = modelCapsOf(lookup, modelName)
  const entry = shareModelEntries(lookup).find((item) => item.id === modelName)
  const account = entry ? `账号 ${entry.account_name}` : null
  if (!caps) return `- ${modelName}${account ? `（${account}）` : ''}`
  const extras = [
    account,
    caps.context_window != null ? `上下文 ${caps.context_window}` : null,
    caps.max_output_tokens != null ? `最大输出 ${caps.max_output_tokens}` : null,
    caps.reasoning ? (caps.reasoning_efforts?.length ? `思考 ${caps.reasoning_efforts.join('/')}` : '思考') : '不思考',
    `输入 ${caps.modalities?.input?.join('/') || 'text'}`,
    `输出 ${caps.modalities?.output?.join('/') || 'text'}`,
  ].filter(Boolean)
  return `- ${modelName}（${extras.join('，')}）`
}

function aiConfigText(lookup: ShareLookup, apiKey: string, modelIds: string[]) {
  const models =
    modelIds.length > 0
      ? modelIds.map((modelName) => modelDetailLine(modelName, lookup)).join('\n')
      : '- （暂无模型列表，请联系管理员先获取模型）'
  return [
    `请先询问我希望为这项配置使用什么名称；默认建议名称是「${lookup.name}」，确认或修改名称后，再将以下 AI 服务接入我正在使用的客户端。根据客户端协议选择对应的 Base URL，不要混用。`,
    '',
    `API Key：${apiKey}`,
    '',
    '可用模型：',
    models,
    '',
    '接入地址（三选一，按客户端协议填写）：',
    '',
    '1) Anthropic / Claude Code',
    `   Base URL：${lookup.gateway.anthropic_base_url}`,
    '   请求路径：POST /v1/messages',
    '   注意：客户端会自己拼 /v1/messages，所以 Base URL 不要带 /v1。',
    '',
    '2) OpenAI 兼容 / Chat Completions（OpenCode、Cursor、ChatBox 等）',
    `   Base URL：${lookup.gateway.openai_base_url}`,
    '   请求路径：POST /v1/chat/completions',
    '',
    '3) OpenAI Responses / Codex CLI',
    `   Base URL：${lookup.gateway.openai_base_url}`,
    '   请求路径：POST /v1/responses',
    '   额外：wire_api 填 responses',
    '',
    '请先确认我用的是哪个客户端，再给出逐步配置方法。配置时直接使用上面的 API Key 和模型名，不要改写。',
  ].join('\n')
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timerRef.current), [])
  async function copy() {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    notifyOk('已复制')
    window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="space-y-1.5">
      <div className="text-xs uppercase tracking-[0.16em] text-mist">{label}</div>
      <div className="flex flex-wrap items-center gap-2 rounded-md bg-ink px-3 py-2 font-mono text-xs break-all">
        <span className="flex-1">{value}</span>
        <Button type="button" variant="line" className="shrink-0 px-2 py-1 text-xs" onClick={() => void copy()}>
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
    </div>
  )
}

export function SharePage() {
  const [rawKey, setRawKey] = useState('')
  const [lookup, setLookup] = useState<ShareLookup | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [dialog, setDialog] = useState<{
    app: string
    label: string
  } | null>(null)
  const [vscodeDialog, setVscodeDialog] = useState(false)
  const [aiConfigDialog, setAiConfigDialog] = useState(false)
  const [aliases, setAliases] = useState<ShareAlias[]>([])

  useEffect(() => listenForShareApiKey(setRawKey), [])

  useEffect(() => {
    const trimmed = rawKey.trim()
    if (trimmed.length < MIN_KEY_LENGTH) {
      setLookup(null)
      setAliases([])
      setError('')
      setLoading(false)
      return
    }
    setLoading(true)
    let cancelled = false
    const timer = window.setTimeout(() => {
      void api
        .shareLookup(trimmed)
        .then((result) => {
          if (cancelled) return
          setLookup(result)
          setAliases(result.aliases ?? [])
          setError('')
        })
        .catch((item: Error) => {
          if (cancelled) return
          setLookup(null)
          setError(item.message)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 400)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [rawKey])

  function openTarget(target: CcSwitchTarget) {
    if (!lookup) return
    if (lookup.status !== 'active') {
      notifyBad('该 Key 已停用，无法导入。')
      return
    }
    if (lookup.models.length === 0) {
      notifyBad('这个 Key 绑定的账号还没有模型列表，请联系管理员先「获取模型」。')
      return
    }
    setDialog({
      app: target.app,
      label: target.label,
    })
  }

  function importToVscode() {
    if (!lookup) return
    if (lookup.status !== 'active') {
      notifyBad('该 Key 已停用，无法导入。')
      return
    }
    if (lookup.models.length === 0) {
      notifyBad('这个 Key 绑定的账号还没有模型列表，请联系管理员先「获取模型」。')
      return
    }
    setVscodeDialog(true)
  }

  function openAiConfig() {
    if (!lookup) return
    if (lookup.models.length === 0) {
      notifyBad('这个 Key 绑定的账号还没有模型列表，请联系管理员先「获取模型」。')
      return
    }
    setAiConfigDialog(true)
  }

  async function copyModel(modelName: string) {
    await navigator.clipboard.writeText(modelName)
    notifyOk(`已复制 ${modelName}`)
  }

  function refreshLookup() {
    const trimmed = rawKey.trim()
    if (trimmed.length < MIN_KEY_LENGTH) {
      notifyBad('请先输入完整 API Key')
      return
    }
    setLookup(null)
    setError('')
    setRawKey('')
    window.setTimeout(() => setRawKey(trimmed), 0)
  }

  async function confirmDialog(values: CcSwitchValues) {
    if (!dialog) return
    try {
      const result = await api.shareCcSwitch({
        api_key: rawKey.trim(),
        app: dialog.app,
        model: values.model,
        haiku_model: dialog.app === 'claude' ? values.haiku || undefined : undefined,
        sonnet_model: dialog.app === 'claude' ? values.sonnet || undefined : undefined,
        opus_model: dialog.app === 'claude' ? values.opus || undefined : undefined,
      })
      notifyInfo(`正在打开 CC Switch（${dialog.label}）。若没反应，请确认已安装 CC Switch。`)
      setDialog(null)
      window.location.href = result.url
    } catch (item) {
      notifyBad(errorMessage(item, '生成导入链接失败'))
    }
  }

  const boundAccounts: ShareLookup['accounts'] = lookup?.accounts.length
    ? lookup.accounts
    : lookup
      ? [{
          id: 0,
          name: lookup.account_name || '未绑定',
          source: lookup.account_source,
          provider: lookup.provider,
          status: lookup.account_status,
          risk_level: lookup.risk_level,
          model_prefix: '',
        }]
      : []
  const usable = Boolean(lookup && lookup.status === 'active' && lookup.models.length > 0 && boundAccounts.some(isAccountAvailable))

  return (
    <div className="page-enter min-h-svh bg-ink px-4 pt-[max(2.5rem,calc(env(safe-area-inset-top)+1.5rem))] pb-[max(2.5rem,calc(env(safe-area-inset-bottom)+1.5rem))]">
      <div className="mx-auto w-full max-w-3xl space-y-5">
        <div>
          <div className="font-mono text-xs tracking-[0.28em] text-signal">PIVOT DESK</div>
          <h1 className="mt-2 text-2xl font-semibold">API Key 自助查询</h1>
          <p className="mt-1 text-sm text-mist">
            把管理员发给你的完整 API Key 粘贴进来，查询绑定账号、可用情况和用量。
          </p>
        </div>

        <Card className="space-y-3">
          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <Field label="API Key">
                <Input
                  value={rawKey}
                  onChange={(event) => setRawKey(event.target.value)}
                  placeholder="sk-…"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                />
              </Field>
            </div>
            <Button type="button" variant="line" className="shrink-0" onClick={refreshLookup} disabled={loading} title="重新查询 API Key">
              <RefreshCw size={16} className={loading ? 'animate-spin' : undefined} />
              刷新
            </Button>
          </div>
          {loading ? <div className="text-sm text-mist">正在识别 Key…</div> : null}
          {error ? <div className="text-sm text-danger">{error}</div> : null}
        </Card>

        {lookup ? (
          <Card className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="text-lg font-medium">{lookup.name}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={lookup.status === 'active' ? 'ok' : 'bad'}>
                  Key {lookup.status === 'active' ? '可用' : '已停用'}
                </Badge>
              </div>
            </div>
            <div className="overflow-hidden rounded-lg border border-line bg-ink/40">
              <div className="border-b border-line px-3 py-2">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1 truncate text-xs uppercase tracking-[0.16em] text-mist">绑定账号</div>
                  <span className="shrink-0 whitespace-nowrap">
                    <Badge tone={boundAccounts.length > 1 ? 'info' : 'mist'}>
                      {boundAccounts.length} 个账号
                    </Badge>
                  </span>
                </div>
                <div className="mt-1 text-xs leading-relaxed text-mist">风险等级来自各上游账号，与本站无关。排第一的账号优先使用。</div>
              </div>
              <ul className="divide-y divide-line">
                {boundAccounts.map((account, index) => (
                  <BoundAccountCard
                    key={`${account.source}-${account.id}-${index}`}
                    account={account}
                    index={index}
                  />
                ))}
              </ul>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-mist">今日 Token</div>
                <div className="mt-2 font-mono text-2xl text-signal">{formatTokenCount(lookup.today_tokens)}</div>
              </div>
              <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-mist">总 Token</div>
                <div className="mt-2 font-mono text-2xl text-signal">{formatTokenCount(lookup.total_tokens)}</div>
              </div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-mist">可用模型</div>
              {lookup.models.length > 0 ? (
                <AccountModelGroups lookup={lookup} />
              ) : (
                <div className="mt-2 text-sm text-warn">还没有模型列表，请让管理员先在上游账号里点「获取模型」。</div>
              )}
            </div>
          </Card>
        ) : null}

        {lookup && lookup.status === 'active' && lookup.models.length > 0 ? (
          <ModelAliasManager rawKey={rawKey.trim()} models={lookup.models} aliases={aliases} onChange={setAliases} />
        ) : null}

        {lookup ? (
          <Card className="space-y-3">
            <div>
              <h2 className="text-lg font-medium">一键导入</h2>
              <p className="mt-1 text-sm text-mist">选择要导入的客户端，再挑选模型。导入后即可直接用上面的 Key。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {lookup.targets.map((target) => (
                <Button
                  key={target.app}
                  type="button"
                  variant="line"
                  disabled={!usable}
                  onClick={() => openTarget(target)}
                >
                  {target.label}
                </Button>
              ))}
              <Button type="button" variant="line" disabled={!usable} onClick={importToVscode}>
                VSCode
              </Button>
            </div>
          </Card>
        ) : null}

        {lookup ? (
          <Card className="space-y-4">
            <div>
              <h2 className="text-lg font-medium">手动配置</h2>
              <p className="mt-1 text-sm text-mist">
                不走 CC Switch 时，按协议填 Base URL。三种协议的地址不一样，不要混用。
              </p>
            </div>
            <CopyField label="API Key" value={rawKey.trim()} />
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-mist">模型（点击复制）</div>
              {lookup.models.length > 0 ? (
                <AccountModelGroups lookup={lookup} onModelClick={(modelName) => void copyModel(modelName)} />
              ) : (
                <div className="mt-2 text-sm text-warn">还没有模型列表，请让管理员先在上游账号里点「获取模型」。</div>
              )}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-3 rounded-lg border border-line bg-ink/40 p-3">
                <div className="font-medium">Anthropic / Claude Code</div>
                <p className="text-xs text-mist">
                  客户端会自己拼 /v1/messages，所以 Base URL 不要带 /v1。
                </p>
                <CopyField label="Base URL" value={lookup.gateway.anthropic_base_url} />
                <div className="text-xs text-mist">请求路径：POST /v1/messages</div>
              </div>
              <div className="space-y-3 rounded-lg border border-line bg-ink/40 p-3">
                <div className="font-medium">OpenAI 兼容 / Chat Completions</div>
                <p className="text-xs text-mist">
                  OpenCode、Grok 以及常见 Chat Completions 客户端，Base URL 要带 /v1。
                </p>
                <CopyField label="Base URL" value={lookup.gateway.openai_base_url} />
                <div className="text-xs text-mist">请求路径：POST /v1/chat/completions</div>
              </div>
              <div className="space-y-3 rounded-lg border border-line bg-ink/40 p-3">
                <div className="font-medium">OpenAI Responses / Codex CLI</div>
                <p className="text-xs text-mist">
                  Codex CLI 等 Responses API 客户端，Base URL 要带 /v1，wire_api 填 responses。
                </p>
                <CopyField label="Base URL" value={lookup.gateway.openai_base_url} />
                <div className="text-xs text-mist">请求路径：POST /v1/responses</div>
              </div>
            </div>
            <div className="text-xs text-mist">
              模型名填上面列出的即可。API Key 就是你刚粘贴的那一串，不要再改。
            </div>
          </Card>
        ) : null}

        {lookup ? (
          <Card className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-medium">AI 配置</h2>
                <p className="mt-1 text-sm text-mist">
                  先勾选模型，再复制说明发给任意 AI。说明里会带上 API Key、Base URL，以及所选模型的上下文、输出、思考和模态。
                </p>
              </div>
              <Button type="button" variant="line" onClick={openAiConfig}>
                选择模型并复制
              </Button>
            </div>
          </Card>
        ) : null}
      </div>

      {aiConfigDialog && lookup ? (
        <ModelPickDialog
          title="复制 AI 配置"
          description="勾选要写进说明的模型。每条会带上上下文、最大输出、思考档位和输入输出模态。"
          models={shareModelEntries(lookup).map((model) => ({
            id: model.id,
            hint: modelHint(modelCapsOf(lookup, model.id)),
            accountName: model.account_name,
            accountIndex: model.account_index,
          }))}
          confirmLabel="复制配置说明"
          successMessage={(count) => `已复制 ${count} 个模型的 AI 配置说明，发给任意 AI 即可。`}
          buildText={(selectedIds) => aiConfigText(lookup, rawKey.trim(), selectedIds)}
          onClose={() => setAiConfigDialog(false)}
        />
      ) : null}
      {vscodeDialog && lookup ? (
        <VscodeImportDialog
          config={lookup.vscode}
          modelEntries={shareModelEntries(lookup)}
          onClose={() => setVscodeDialog(false)}
        />
      ) : null}
      {dialog && lookup ? (
        <CcSwitchDialog
          label={dialog.label}
          models={shareModelEntries(lookup).map((model) => ({
            id: model.id,
            accountName: model.account_name,
            accountIndex: model.account_index,
          }))}
          isClaude={dialog.app === 'claude'}
          initial={{ model: lookup.models[0] ?? '', haiku: '', sonnet: '', opus: '' }}
          onConfirm={(values) => void confirmDialog(values)}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  )
}
