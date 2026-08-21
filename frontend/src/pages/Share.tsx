import { useEffect, useRef, useState } from 'react'
import { CcSwitchDialog, type CcSwitchValues } from '../components/CcSwitchDialog'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api, type CcSwitchTarget, type ShareLookup } from '../lib/api'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { MIN_KEY_LENGTH, errorMessage } from '../lib/utils'

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

  async function copyModel(modelName: string) {
    await navigator.clipboard.writeText(modelName)
    notifyOk(`已复制 ${modelName}`)
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

  const usable = lookup && lookup.status === 'active' && lookup.account_status === 'active'

  return (
    <div className="page-enter min-h-svh px-4 py-10">
      <div className="mx-auto w-full max-w-3xl space-y-5">
        <div>
          <div className="font-mono text-xs tracking-[0.28em] text-signal">SIGNAL DESK</div>
          <h1 className="mt-2 text-2xl font-semibold">API Key 自助查询</h1>
          <p className="mt-1 text-sm text-mist">
            把管理员发给你的完整 API Key 粘贴进来，查询绑定的上游账号、可用情况和用量。
          </p>
        </div>

        <Card className="space-y-3">
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
          {loading ? <div className="text-sm text-mist">正在识别 Key…</div> : null}
          {error ? <div className="text-sm text-danger">{error}</div> : null}
        </Card>

        {lookup ? (
          <Card className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="text-lg font-medium">{lookup.name}</div>
                <div className="mt-1 text-sm text-mist">
                  绑定上游：{lookup.account_name || '未绑定'} · {lookup.provider_label}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={lookup.status === 'active' ? 'ok' : 'bad'}>
                  Key {lookup.status === 'active' ? '可用' : '已停用'}
                </Badge>
                <Badge tone={lookup.account_status === 'active' ? 'ok' : 'bad'}>
                  账号 {lookup.account_status === 'active' ? '可用' : '不可用'}
                </Badge>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-mist">今日 Token</div>
                <div className="mt-2 font-mono text-2xl text-signal">{lookup.today_tokens}</div>
              </div>
              <div className="rounded-lg border border-line bg-ink/40 px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-mist">总 Token</div>
                <div className="mt-2 font-mono text-2xl text-signal">{lookup.total_tokens}</div>
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
