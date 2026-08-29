import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api, type CcSwitchTarget, type ShareLookup } from '../lib/api'
import { listenForShareApiKey } from '../lib/shareTransfer'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { MIN_KEY_LENGTH, cn, errorMessage, formatTokenCount } from '../lib/utils'

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

function aiConfigText(lookup: ShareLookup, apiKey: string) {
  const models =
    lookup.models.length > 0
      ? lookup.models.map((modelName) => `- ${modelName}`).join('\n')
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
  const [aiCopied, setAiCopied] = useState(false)
  const aiCopiedTimerRef = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(aiCopiedTimerRef.current), [])

  useEffect(() => listenForShareApiKey(setRawKey), [])

  useEffect(() => {
    const trimmed = rawKey.trim()
    if (trimmed.length < MIN_KEY_LENGTH) {
      setLookup(null)
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

  async function importToVscode() {
    if (!lookup) return
    if (lookup.status !== 'active') {
      notifyBad('该 Key 已停用，无法导入。')
      return
    }
    if (lookup.models.length === 0) {
      notifyBad('这个 Key 绑定的账号还没有模型列表，请联系管理员先「获取模型」。')
      return
    }
    try {
      const text = JSON.stringify(lookup.vscode, null, 2)
      await navigator.clipboard.writeText(text)
      notifyOk('VSCode 配置已复制，粘贴到 chatLanguageModels.json 即可。')
    } catch (item) {
      notifyBad(errorMessage(item, '复制 VSCode 配置失败'))
    }
  }

  async function copyAiConfig() {
    if (!lookup) return
    try {
      await navigator.clipboard.writeText(aiConfigText(lookup, rawKey.trim()))
      setAiCopied(true)
      notifyOk('已复制 AI 配置说明，发给任意 AI 即可。')
      window.clearTimeout(aiCopiedTimerRef.current)
      aiCopiedTimerRef.current = window.setTimeout(() => setAiCopied(false), 1500)
    } catch (item) {
      notifyBad(errorMessage(item, '复制 AI 配置说明失败'))
    }
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
    <div className="page-enter min-h-svh px-4 py-10">
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
            <div className="rounded-lg border border-line bg-ink/30 p-3">
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-mist">绑定账号风险</div>
                  <div className="mt-1 text-sm text-mist">风险等级来自各上游账号，与本站无关。</div>
                </div>
                <Badge tone={lookup.accounts.length > 1 ? 'info' : 'mist'}>
                  {lookup.accounts.length} 个账号
                </Badge>
              </div>
              <div className="mt-3 grid gap-2">
                {boundAccounts.map((account, index) => {
                  const risk = RISK_META[account.risk_level]
                  return (
                    <div
                      key={account.id}
                      className={cn('flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2.5', risk?.className ?? 'border-line bg-ink/40 text-mist')}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 font-medium">
                          <span>{accountSourceLabel(account.source)} {account.name}</span>
                          <Badge tone={isAccountAvailable(account) ? 'ok' : 'bad'}>
                            {accountStatusLabel(account)}
                          </Badge>
                          {index === 0 ? <Badge tone="info">优先使用</Badge> : <span className="text-xs opacity-70">第 {index + 1} 个</span>}
                        </div>
                        <div className="mt-1 text-xs opacity-70">模型前缀：{'model_prefix' in account && account.model_prefix ? account.model_prefix : '自动生成'}</div>
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="font-semibold">{risk?.label ?? account.risk_level}</div>
                        <div className="mt-1 max-w-xs text-xs opacity-70">{risk?.hint ?? ''}</div>
                      </div>
                    </div>
                  )
                })}
              </div>
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
                <div className="mt-2 flex flex-wrap gap-2">
                  {lookup.models.map((modelName) => (
                    <span key={modelName} className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-xs">
                      {modelName}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="mt-2 text-sm text-warn">还没有模型列表，请让管理员先在上游账号里点「获取模型」。</div>
              )}
            </div>
          </Card>
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
              <Button type="button" variant="line" disabled={!usable} onClick={() => void importToVscode()}>
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
                <div className="mt-2 flex flex-wrap gap-2">
                  {lookup.models.map((modelName) => (
                    <button
                      key={modelName}
                      type="button"
                      onClick={() => void copyModel(modelName)}
                      className="rounded-full bg-white/5 px-2 py-0.5 font-mono text-xs transition hover:bg-white/10 hover:text-signal"
                    >
                      {modelName}
                    </button>
                  ))}
                </div>
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
                  复制这段说明发给任意 AI，让它按你的客户端帮你配好供应商。包含 API Key、Base URL 和可用模型。
                </p>
              </div>
              <Button type="button" variant="line" onClick={() => void copyAiConfig()}>
                {aiCopied ? '已复制' : '复制配置说明'}
              </Button>
            </div>
          </Card>
        ) : null}
      </div>

      {dialog && lookup ? (
        <CcSwitchDialog
          label={dialog.label}
          models={lookup.models}
          isClaude={dialog.app === 'claude'}
          initial={{ model: lookup.models[0] ?? '', haiku: '', sonnet: '', opus: '' }}
          onConfirm={(values) => void confirmDialog(values)}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  )
}
