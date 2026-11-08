import { useQuery, useQueryClient } from '@tanstack/react-query'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import {
  Activity,
  Coins,
  ExternalLink,
  FileJson,
  Heart,
  KeyRound,
  ListTree,
  MoreHorizontal,
  Pencil,
  Power,
  Trash2,
  Upload,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Select, Sheet, Switch } from '../components/ui'
import { api, type Account, type AccountUsage, type ModelCaps, type Provider, type QuotaItem } from '../lib/api'
import { notifyBad, notifyInfo, notifyOk } from '../lib/toast'
import { Can, useCan } from '../lib/session'
import { MIN_PASSWORD_LENGTH, RISK_LEVELS, cn, errorMessage, formatEmbeddedTimes, formatTime, modelCapsHint } from '../lib/utils'
import { FilterBar } from '../components/filter-bar'
import { InfoMasonry } from '../components/InfoMasonry'

const QUOTA_WARN_HIGH = 90
const QUOTA_WARN_MEDIUM = 70

const HEADER_SPOOF_OPTIONS = [
  { value: 'none', label: '关闭' },
  { value: 'grok', label: 'Grok CLI' },
  { value: 'opencode', label: 'OpenCode' },
  { value: 'claude_code', label: 'Claude Code' },
  { value: 'codex', label: 'Codex CLI' },
] as const

type HeaderSpoof = 'none' | 'grok' | 'opencode' | 'claude_code' | 'codex'

const HEADER_SPOOF_LABELS: Record<HeaderSpoof, string> = {
  none: '',
  grok: '伪装 Grok CLI',
  opencode: '伪装 OpenCode',
  claude_code: '伪装 Claude Code',
  codex: '伪装 Codex CLI',
}

function defaultHeaderSpoof(providerId: string): HeaderSpoof {
  if (providerId === 'grok') return 'grok'
  if (providerId === 'opencode_go') return 'opencode'
  return 'none'
}

const PROVIDER_SWITCH_RISKS = [
  '保存后会用新供应商的默认 Base URL 和请求头伪装覆盖当前值，并清空已拉取的模型列表，需要重新拉取；期间依赖这些模型的路由会暂时不可用。',
  '现有 API Key 会保留，它属于原供应商，对新供应商通常无效，请在切换后更新，否则请求会失败。',
  '风险等级不会自动调整，若新供应商来源不同，请按实际情况修改。',
  '切到需要 OAuth 的供应商后，需要重新点「去授权」完成授权。',
]

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
  const [providerTouched, setProviderTouched] = useState(false)
  const [baseUrl, setBaseUrl] = useState(account?.base_url ?? '')
  const [websiteUrl, setWebsiteUrl] = useState(account?.website_url ?? '')
  const [apiKey, setApiKey] = useState('')
  const [riskLevel, setRiskLevel] = useState(account?.risk_level ?? 'low')
  const [modelPrefix, setModelPrefix] = useState(account?.model_prefix ?? '')
  const [headerSpoof, setHeaderSpoof] = useState<HeaderSpoof>(
    account?.header_spoof ?? defaultHeaderSpoof(account?.provider ?? 'deepseek'),
  )
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [confirmSwitch, setConfirmSwitch] = useState(false)

  const preset = useMemo(() => providers.find((item) => item.id === provider), [providers, provider])
  const authType = preset?.auth_type ?? (editing ? account.auth_type : undefined)
  // 编辑时只有用户主动切换供应商才覆盖 Base URL / 请求头伪装，避免打开弹窗即被重置
  const autoFillPreset = !editing || providerTouched
  const providerChanged = editing && provider !== account.provider
  const oldProviderLabel = editing ? providers.find((item) => item.id === account.provider)?.label ?? account.provider : ''

  useEffect(() => {
    if (!autoFillPreset) return
    if (preset?.base_url) setBaseUrl(preset.base_url)
  }, [autoFillPreset, preset?.base_url])

  useEffect(() => {
    if (!autoFillPreset) return
    setHeaderSpoof(defaultHeaderSpoof(provider))
  }, [autoFillPreset, provider])

  async function save() {
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError('请填写显示名')
      return
    }
    if (providerChanged) {
      setConfirmSwitch(true)
      return
    }
    await applySave()
  }

  async function applySave() {
    const trimmedName = name.trim()
    const trimmedUrl = baseUrl.trim()
    const trimmedWebsiteUrl = websiteUrl.trim()
    setPending(true)
    setError('')
    try {
      if (editing) {
        await api.updateAccount(account.id, {
          name: trimmedName,
          provider,
          base_url: trimmedUrl || undefined,
          website_url: trimmedWebsiteUrl || null,
          api_key: authType === 'api_key' && apiKey.trim() ? apiKey.trim() : undefined,
          risk_level: riskLevel,
          model_prefix: modelPrefix.trim() || undefined,
          header_spoof: headerSpoof,
        })
        onSaved('账号已更新')
      } else {
        await api.createAccount({
          name: trimmedName,
          provider,
          base_url: trimmedUrl || undefined,
          website_url: trimmedWebsiteUrl || undefined,
          api_key: authType === 'api_key' ? apiKey : undefined,
          risk_level: riskLevel,
          model_prefix: modelPrefix.trim() || undefined,
          header_spoof: headerSpoof,
        })
        onSaved('账号已创建')
      }
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  if (confirmSwitch) {
    return (
      <Dialog title="确认切换供应商" onClose={onClose} className="max-w-xl">
        <div className="grid gap-4">
          <p className="text-sm leading-6 text-paper">
            确定把「{account?.name ?? ''}」的供应商从 {oldProviderLabel} 切换为 {preset?.label ?? provider}？切换会影响以下配置，请确认已知晓风险。
          </p>
          <ul className="grid list-disc gap-2 pl-5 text-sm leading-6 text-warn">
            {PROVIDER_SWITCH_RISKS.map((risk) => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" disabled={pending} onClick={() => setConfirmSwitch(false)}>
              返回修改
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={pending}
              onClick={() => {
                setConfirmSwitch(false)
                void applySave()
              }}
            >
              {pending ? '保存中…' : '确认切换'}
            </Button>
          </div>
        </div>
      </Dialog>
    )
  }

  return (
    <Sheet
      title={editing ? '编辑账号' : '新建账号'}
      onClose={onClose}
      size="lg:max-w-2xl"
      mobileFull
      footer={
        <div className="flex flex-col gap-2">
          {error ? (
            <div className="text-sm text-danger" role="alert">
              {error}
            </div>
          ) : null}
          {/* 移动端取消/保存同排，避免长表单里两个整宽按钮各占一行 */}
          <div className="flex items-center justify-end gap-2">
            <Button type="button" variant="ghost" className="flex-1 sm:flex-none" onClick={onClose}>
              取消
            </Button>
            <Button
              type="button"
              className="flex-1 sm:flex-none"
              disabled={!name.trim() || pending}
              onClick={() => void save()}
            >
              保存
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="显示名">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="DeepSeek 主号" />
        </Field>
        <Field label="供应商">
          <Select
            value={provider}
            onChange={(event) => {
              setProviderTouched(true)
              setProvider(event.target.value)
            }}
          >
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </Select>
          {providerChanged ? (
            <div className="mt-1.5 rounded-md border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs leading-5 text-warn">
              已从 {oldProviderLabel} 切换到 {preset?.label ?? provider}。保存前会再次确认，切换会更新默认 Base URL 与请求头伪装、清空已拉取的模型列表，且原 API Key 对新供应商通常无效。
            </div>
          ) : editing ? (
            <div className="mt-1.5 text-xs text-mist">
              切换供应商会更新默认 Base URL 与请求头伪装，并清空已拉取的模型列表，需重新拉取。
            </div>
          ) : null}
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
        <div className="sm:col-span-2">
          <Field label="官网地址">
            <Input
              value={websiteUrl}
              onChange={(event) => setWebsiteUrl(event.target.value)}
              placeholder="https://..."
              type="url"
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
        <div className="sm:col-span-2">
          <Field label="模型前缀">
            <Input
              className="font-mono"
              value={modelPrefix}
              onChange={(event) => setModelPrefix(event.target.value)}
              placeholder="留空则按显示名自动生成"
            />
          </Field>
          <div className="mt-1.5 text-xs text-mist">
            同一 Key 绑定多个账号且模型名冲突时，后绑账号使用此前缀，例如 prefix/model。仅字母数字、下划线和短横线，最长 32 位。
          </div>
        </div>
        <div className="sm:col-span-2">
          <Field label="请求头伪装">
            <Select value={headerSpoof} onChange={(event) => setHeaderSpoof(event.target.value as typeof headerSpoof)}>
              {HEADER_SPOOF_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="mt-1.5 text-xs text-mist">
            转向上游时带上对应客户端的请求头。Grok 额度查询走 cli-chat-proxy 时需要 Grok CLI 伪装。
          </div>
        </div>
        <div className="min-w-0 sm:col-span-2">
          <Field label="风险等级">
            <Select value={riskLevel} onChange={(event) => setRiskLevel(event.target.value)}>
              <option value="low">{RISK_LEVELS.low.label}</option>
              <option value="medium">{RISK_LEVELS.medium.label}</option>
              <option value="high">{RISK_LEVELS.high.label}</option>
            </Select>
          </Field>
          <div className="mt-1.5 break-words text-xs text-mist">
            {RISK_LEVELS[riskLevel]?.hint ?? '风险指上游账号来源，与本站无关。'}
          </div>
        </div>
      </div>
    </Sheet>
  )
}

function ExportDialog({ onClose }: { onClose: () => void }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit() {
    if (password.length < MIN_PASSWORD_LENGTH) {
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
      setError(errorMessage(caught, '导出失败'))
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
    if (password.length < MIN_PASSWORD_LENGTH) {
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
      setError(errorMessage(caught, '导入失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="导入上游账号" onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-sm text-mist">选择加密导出的 JSON，输入当时的密码。重名会新建为「原名（1）」。</p>
        <Field label="JSON 文件">
          <label
            className={cn(
              'flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-md border border-dashed border-line bg-ink px-3 py-6 text-center transition hover:border-signal/60 hover:bg-elevate',
              fileName && 'border-signal/40',
            )}
          >
            <input
              type="file"
              accept="application/json,.json"
              className="sr-only"
              onChange={(event) => void onFile(event)}
            />
            {fileName ? (
              <>
                <FileJson className="h-5 w-5 text-signal" />
                <span className="max-w-full truncate font-mono text-xs text-paper">{fileName}</span>
                <span className="text-xs text-mist">点击可重新选择</span>
              </>
            ) : (
              <>
                <Upload className="h-5 w-5 text-mist" />
                <span className="text-sm text-paper">点击选择 JSON 文件</span>
                <span className="text-xs text-mist">支持 .json 格式</span>
              </>
            )}
          </label>
        </Field>
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
          const tone = used >= QUOTA_WARN_HIGH ? 'bg-danger' : used >= QUOTA_WARN_MEDIUM ? 'bg-warn' : 'bg-signal'
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
            <span className="font-mono text-xs text-mist">{formatEmbeddedTimes(String(item.value))}</span>
          </div>
        )
      })}
    </div>
  )
}

const MODELS_COLLAPSED_COUNT = 8

type AccountAction = {
  key: string
  label: string
  icon: LucideIcon
  onSelect: () => void
  danger?: boolean
}

function AccountActionButton({
  action,
  disabled,
  className,
}: {
  action: AccountAction
  disabled?: boolean
  className?: string
}) {
  const Icon = action.icon
  return (
    <Button
      type="button"
      variant={action.danger ? 'danger' : 'line'}
      size="sm"
      className={cn('min-w-0 shrink-0', className)}
      disabled={disabled}
      onClick={action.onSelect}
    >
      <Icon size={14} />
      {action.label}
    </Button>
  )
}

function AccountActionMenu({ actions }: { actions: AccountAction[] }) {
  if (!actions.length) return null
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button type="button" variant="line" size="sm" className="shrink-0">
          <MoreHorizontal size={14} />
          更多
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          collisionPadding={12}
          className={cn(
            'z-[70] max-h-[min(70dvh,20rem)] min-w-40 overflow-y-auto overscroll-contain rounded-md border border-line bg-panel-2 p-1 shadow-menu',
            'motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95 motion-safe:data-[state=open]:duration-150',
          )}
        >
          {actions.map((action) => {
            const Icon = action.icon
            return (
              <DropdownMenu.Item
                key={action.key}
                onSelect={action.onSelect}
                className={cn(
                  'flex cursor-pointer select-none items-center gap-2 rounded-md px-3 py-2 text-sm outline-none transition',
                  action.danger
                    ? 'bg-danger/15 text-danger data-[highlighted]:bg-danger/25'
                    : 'text-paper data-[highlighted]:bg-elevate',
                )}
              >
                <Icon size={14} />
                {action.label}
              </DropdownMenu.Item>
            )
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

function AccountActionRow({ actions, disabled }: { actions: AccountAction[]; disabled?: boolean }) {
  const rowRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(actions.length)

  useEffect(() => {
    const row = rowRef.current
    const measure = measureRef.current
    if (!row || !measure) return
    const update = () => {
      const widths = Array.from(measure.children, (node) => (node as HTMLElement).offsetWidth)
      const gap = 8
      const moreWidth = widths[widths.length - 1] ?? 0
      const actionWidths = widths.slice(0, actions.length)
      let used = 0
      let count = 0
      for (let index = 0; index < actionWidths.length; index += 1) {
        const remaining = actionWidths.length - index - 1
        const next = used + (count > 0 ? gap : 0) + actionWidths[index]
        const reserved = remaining > 0 ? gap + moreWidth : 0
        if (next + reserved > row.clientWidth && count > 0) break
        used = next
        count += 1
      }
      setVisibleCount(count)
    }
    const observer = new ResizeObserver(update)
    observer.observe(row)
    update()
    return () => observer.disconnect()
  }, [actions])

  if (!actions.length) return null
  const inline = actions.slice(0, visibleCount)
  const overflow = actions.slice(visibleCount)
  return (
    <div ref={rowRef} className="relative min-w-0">
      <div
        ref={measureRef}
        aria-hidden="true"
        className="pointer-events-none invisible absolute flex w-max gap-2"
      >
        {actions.map((action) => (
          <AccountActionButton key={action.key} action={action} />
        ))}
        <Button type="button" variant="line" size="sm" className="shrink-0">
          <MoreHorizontal size={14} />
          更多
        </Button>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        {inline.map((action) => (
          <AccountActionButton key={action.key} action={action} disabled={disabled} />
        ))}
        <AccountActionMenu actions={overflow} />
      </div>
    </div>
  )
}

function PinButton({
  pinned,
  disabled,
  onToggle,
}: {
  pinned: boolean
  disabled?: boolean
  onToggle: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-pressed={pinned}
      aria-label={pinned ? '取消特别关心' : '特别关心'}
      title={pinned ? '取消特别关心' : '特别关心'}
      disabled={disabled}
      onClick={onToggle}
      className={cn('shrink-0', pinned ? 'text-signal' : 'text-mist')}
    >
      <Heart size={16} className={cn(pinned && 'fill-signal')} />
    </Button>
  )
}

function ModelList({
  models,
  expanded,
  onToggle,
  onEdit,
  onToggleEnabled,
}: {
  models: ModelCaps[]
  expanded: boolean
  onToggle: () => void
  onEdit: (model: ModelCaps) => void
  onToggleEnabled: (model: ModelCaps) => void
}) {
  const canManage = useCan('accounts:models_manage')
  const visible = expanded ? models : models.slice(0, MODELS_COLLAPSED_COUNT)
  const hasMore = models.length > MODELS_COLLAPSED_COUNT
  const enabledCount = models.filter((model) => model.enabled !== false).length

  return (
    <div>
      <div className="flex flex-col gap-1.5">
        {visible.map((model) => {
          const enabled = model.enabled !== false
          const hint = modelCapsHint(model)
          const content = (
            <>
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span
                  className={cn(
                    'block min-w-0 truncate font-mono text-xs',
                    enabled ? 'text-paper' : 'text-mist',
                  )}
                  title={model.id}
                >
                  {model.id}
                </span>
                {hint || canManage ? (
                  <span className="block min-w-0 truncate text-[11px] leading-4 text-mist" title={hint || undefined}>
                    {hint || '点击修改能力'}
                  </span>
                ) : null}
              </span>
              <span className="flex shrink-0 items-center">
                {model.overridden?.length ? (
                  <Badge tone="warn">已覆盖</Badge>
                ) : model.source === 'custom' ? (
                  <Badge tone="info" title="上游 /models 未返回，手工添加">
                    自定义
                  </Badge>
                ) : (
                  <Badge tone="info">识别</Badge>
                )}
              </span>
            </>
          )
          return (
            <div
              key={model.id}
              className={cn(
                'flex min-w-0 items-center gap-2 overflow-hidden rounded-md border px-2.5 py-1.5',
                enabled ? 'border-line bg-ink/40' : 'border-dashed border-line/80 bg-ink/20',
              )}
            >
              {canManage ? (
                <button
                  type="button"
                  onClick={() => onEdit(model)}
                  className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-left hover:text-paper"
                >
                  {content}
                </button>
              ) : (
                <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">{content}</div>
              )}
              {canManage ? <Switch checked={enabled} onCheckedChange={() => onToggleEnabled(model)} /> : null}
            </div>
          )
        })}
      </div>
      {hasMore ? (
        <button
          type="button"
          onClick={onToggle}
          className="mt-2 text-xs text-mist underline-offset-2 hover:text-paper hover:underline"
        >
          {expanded ? '收起' : `展开全部 ${models.length} 个`}
        </button>
      ) : null}
      {enabledCount !== models.length ? (
        <div className="mt-1 text-xs text-mist">已关闭 {models.length - enabledCount} 个，对外不可见</div>
      ) : null}
    </div>
  )
}

function ModelOverrideDialog({
  accountId,
  model,
  onClose,
  onSaved,
}: {
  accountId: number
  model: ModelCaps
  onClose: () => void
  onSaved: () => void
}) {
  const [contextWindow, setContextWindow] = useState(model.context_window ? String(model.context_window) : '')
  const [maxOutput, setMaxOutput] = useState(model.max_output_tokens ? String(model.max_output_tokens) : '')
  const [reasoning, setReasoning] = useState(model.reasoning)
  const [efforts, setEfforts] = useState((model.reasoning_efforts ?? []).join(', '))
  const [vision, setVision] = useState((model.modalities?.input ?? []).includes('image'))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const removable = model.source === 'custom' || model.source === 'manual'

  async function save() {
    setPending(true)
    setError('')
    try {
      const parsedContext = contextWindow.trim() ? Number(contextWindow.trim()) : null
      const parsedOutput = maxOutput.trim() ? Number(maxOutput.trim()) : null
      if (parsedContext != null && (!Number.isInteger(parsedContext) || parsedContext <= 0)) {
        setError('上下文窗口必须是正整数')
        return
      }
      if (parsedOutput != null && (!Number.isInteger(parsedOutput) || parsedOutput <= 0)) {
        setError('最大输出必须是正整数')
        return
      }
      const effortValues = efforts
        .split(/[,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
      await api.updateAccountModel(accountId, model.id, {
        context_window: parsedContext,
        max_output_tokens: parsedOutput,
        reasoning,
        reasoning_efforts: reasoning ? effortValues : [],
        modalities: { input: vision ? ['text', 'image'] : ['text'], output: ['text'] },
      })
      notifyOk('模型能力已更新')
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  async function reset() {
    setPending(true)
    setError('')
    try {
      await api.updateAccountModel(accountId, model.id, { clear: true })
      notifyOk('已恢复自动识别')
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '恢复失败'))
    } finally {
      setPending(false)
    }
  }

  async function remove() {
    if (!window.confirm(`删除模型 ${model.id}？上游刷新时不会自动恢复该模型。`)) return
    setPending(true)
    setError('')
    try {
      await api.removeAccountModel(accountId, model.id)
      notifyOk('模型已删除')
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '删除失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="修改模型能力" onClose={onClose}>
      <div className="grid gap-3">
        <div className="truncate font-mono text-sm text-paper" title={model.id}>
          {model.id}
        </div>
        <p className="text-xs leading-5 text-mist">这里改的是本站保存的窗口、思考和模态，刷新模型列表时不会覆盖你的手改。</p>
      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          <Field label="上下文窗口">
            <Input value={contextWindow} onChange={(event) => setContextWindow(event.target.value)} placeholder="128000" inputMode="numeric" />
          </Field>
          <Field label="最大输出">
            <Input value={maxOutput} onChange={(event) => setMaxOutput(event.target.value)} placeholder="16000" inputMode="numeric" />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm text-paper">
          <input type="checkbox" checked={reasoning} onChange={(event) => setReasoning(event.target.checked)} />
          支持思考
        </label>
        <Field label="思考档位（可选，逗号分隔）">
          <Input value={efforts} disabled={!reasoning} onChange={(event) => setEfforts(event.target.value)} placeholder="low, medium, high" />
        </Field>
        <label className="flex items-center gap-2 text-sm text-paper">
          <input type="checkbox" checked={vision} onChange={(event) => setVision(event.target.checked)} />
          支持视觉
        </label>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          {removable ? (
            <Button type="button" variant="danger" className="mr-auto" disabled={pending} onClick={() => void remove()}>
              删除模型
            </Button>
          ) : null}
          <Button type="button" variant="ghost" disabled={pending} onClick={() => void reset()}>
            恢复识别
          </Button>
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void save()}>
            {pending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

const USAGE_KIND_LABEL: Record<string, string> = {
  api_key: 'API Key',
  skill_classification: 'Skill 自动分类',
  skill_report: 'Skill 分析报告',
  knowledge_base: '知识库',
  voice_room: '语音房',
  request_log: '请求日志',
  oauth: 'OAuth 授权',
}

function DeleteAccountDialog({
  account,
  onClose,
  onDeleted,
}: {
  account: Account
  onClose: () => void
  onDeleted: () => void
}) {
  const { data: usage, isLoading, error } = useQuery({
    queryKey: ['account-usage', account.id],
    queryFn: () => api.accountUsage(account.id),
  })
  const [step, setStep] = useState<'review' | 'confirm'>('review')
  const [pending, setPending] = useState(false)

  async function confirmDelete() {
    setPending(true)
    try {
      await api.deleteAccount(account.id)
      notifyOk(`已删除 ${account.name}`)
      onDeleted()
    } catch (caught) {
      notifyBad(errorMessage(caught, '删除失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title={step === 'review' ? `删除账号「${account.name}」` : '再次确认删除'} onClose={onClose} className="max-w-xl">
      {step === 'review' ? (
        <DeleteAccountReview
          usage={usage}
          loading={isLoading}
          error={error ? errorMessage(error, '无法加载关联信息') : ''}
          onCancel={onClose}
          onContinue={() => setStep('confirm')}
        />
      ) : (
        <div className="grid gap-4">
          <p className="text-sm leading-6 text-paper">
            确定删除上游账号「{account.name}」？删除后无法恢复，关联功能会按上一步说明解绑或关闭。
          </p>
          {usage?.has_relations ? (
            <p className="text-sm leading-6 text-warn">当前仍有功能绑定这个账号，删除后这些功能会立即失效。</p>
          ) : (
            <p className="text-sm leading-6 text-mist">当前没有其它功能绑定这个账号，删除后凭证也会一并清除。</p>
          )}
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" disabled={pending} onClick={() => setStep('review')}>
              返回查看
            </Button>
            <Button type="button" variant="danger" disabled={pending} onClick={() => void confirmDelete()}>
              {pending ? '删除中…' : '确认删除'}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

function DeleteAccountReview({
  usage,
  loading,
  error,
  onCancel,
  onContinue,
}: {
  usage?: AccountUsage
  loading: boolean
  error: string
  onCancel: () => void
  onContinue: () => void
}) {
  return (
    <div className="grid gap-4">
      {loading ? <p className="text-sm text-mist">正在检查这个账号被哪些功能使用…</p> : null}
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      {usage ? (
        <>
          {usage.items.length ? (
            <div className="space-y-2">
              <p className="text-sm text-paper">删除后会处理这些关联：</p>
              <ul className="max-h-56 space-y-2 overflow-y-auto">
                {usage.items.map((item) => (
                  <li
                    key={`${item.kind}-${item.id ?? item.name}`}
                    className="rounded-lg border border-line bg-ink/40 px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={item.severity === 'warning' ? 'warn' : 'mist'}>
                        {USAGE_KIND_LABEL[item.kind] || item.kind}
                      </Badge>
                      <span className="text-sm font-medium text-paper">{item.name}</span>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-mist">{item.detail}</p>
                    <p className="mt-1 text-xs leading-5 text-paper">{item.action}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm leading-6 text-mist">没有 API Key、Skill、知识库或语音房绑定这个账号。</p>
          )}
          <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2">
            <p className="text-xs uppercase tracking-[0.16em] text-danger">删除风险</p>
            <ul className="mt-2 space-y-1.5 text-sm leading-6 text-paper">
              {usage.risks.map((risk) => (
                <li key={risk}>{risk}</li>
              ))}
            </ul>
          </div>
        </>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button type="button" variant="danger" disabled={loading || Boolean(error) || !usage} onClick={onContinue}>
          继续删除
        </Button>
      </div>
    </div>
  )
}

function CustomModelDialog({
  accountId,
  onClose,
  onSaved,
}: {
  accountId: number
  onClose: () => void
  onSaved: (created: boolean) => void
}) {
  const [modelId, setModelId] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  async function save() {
    const cleaned = modelId.trim()
    if (!cleaned) {
      setError('请输入模型 ID')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.addAccountModel(accountId, cleaned)
      onSaved(result.created)
    } catch (caught) {
      setError(errorMessage(caught, '添加失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="添加自定义模型" onClose={onClose}>
      <div className="grid gap-3">
        <p className="text-xs leading-5 text-mist">
          上游 <span className="font-mono text-paper">/models</span> 常漏掉向量等模型，可在这里手工补录。添加后在列表中点击即可修改能力。
        </p>
        <Field label="模型 ID">
          <Input
            ref={inputRef}
            value={modelId}
            onChange={(event) => setModelId(event.target.value)}
            placeholder="embedding-3"
            onKeyDown={(event) => {
              if (event.key === 'Enter') void save()
            }}
          />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void save()}>
            {pending ? '添加中…' : '添加'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function estimateAccountCardHeight(account: Account, providers: Provider[]): number {
  // 粗略估算，仅用于瀑布流分配到最短列；偏差只会让列底轻微不齐。
  let height = 160
  if (account.website_url) height += 24
  if (providers.find((item) => item.id === account.provider)?.supports_quota !== false) {
    height += 69
    for (const item of account.quota?.items ?? []) {
      height += item.type === 'progress' ? 44 : 20
    }
  }
  return height
}

export function AccountsPage() {
  const { accountId } = useParams()
  const selectedId = accountId ? Number(accountId) : null
  const canQuota = useCan('accounts:quota')
  const canModels = useCan('accounts:models_manage')
  const canProbe = useCan('accounts:probe')
  const canUpdate = useCan('accounts:update')
  const canDelete = useCan('accounts:delete')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: api.providers })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const [editor, setEditor] = useState<Account | 'new' | null>(null)
  const [transfer, setTransfer] = useState<'export' | 'import' | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [oauthPaste, setOauthPaste] = useState('')
  const [oauthDialog, setOauthDialog] = useState(false)
  const [oauthAccountId, setOauthAccountId] = useState<number | null>(null)
  const [expandedModels, setExpandedModels] = useState<Set<number>>(new Set())
  const [editingModel, setEditingModel] = useState<{ accountId: number; model: ModelCaps } | null>(null)
  const [customModelAccountId, setCustomModelAccountId] = useState<number | null>(null)
  const [deletingAccount, setDeletingAccount] = useState<Account | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [providerFilter, setProviderFilter] = useState('')
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

  const filteredAccounts = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return accounts.filter((account) => {
      if (statusFilter && account.status !== statusFilter) return false
      if (providerFilter && account.provider !== providerFilter) return false
      if (!keyword) return true
      return (
        account.name.toLowerCase().includes(keyword) ||
        account.provider.toLowerCase().includes(keyword) ||
        account.base_url.toLowerCase().includes(keyword)
      )
    })
  }, [accounts, search, statusFilter, providerFilter])

  async function runProbe(id: number) {
    setBusyId(id)
    try {
      const result = await api.probe(id)
      if (result.ok) notifyOk(`探测成功 ${result.latency_ms}ms`)
      else notifyBad(`探测失败：${result.message}`)
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } catch (error) {
      notifyBad(errorMessage(error, '探测失败'))
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
      notifyBad(errorMessage(error, '额度查询失败'))
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
      notifyBad(errorMessage(error, '拉取模型失败'))
    } finally {
      setBusyId(null)
    }
  }

  async function toggleModelEnabled(accountId: number, model: ModelCaps) {
    const enabled = model.enabled !== false
    try {
      await api.updateAccountModel(accountId, model.id, { enabled: !enabled })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
      notifyOk(enabled ? `已关闭 ${model.id}` : `已启用 ${model.id}`)
    } catch (error) {
      notifyBad(errorMessage(error, '更新模型状态失败'))
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
      notifyBad(errorMessage(error, '无法开始授权'))
    } finally {
      setBusyId(null)
    }
  }

  async function toggle(account: Account) {
    setBusyId(account.id)
    try {
      await api.updateAccount(account.id, { status: account.status === 'active' ? 'disabled' : 'active' })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    } catch (error) {
      notifyBad(errorMessage(error, '操作失败'))
    } finally {
      setBusyId(null)
    }
  }

  async function togglePinned(account: Account) {
    setBusyId(account.id)
    try {
      await api.updateAccount(account.id, { pinned: !account.pinned })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      notifyOk(account.pinned ? `已取消特别关心 ${account.name}` : `已特别关心 ${account.name}`)
    } catch (error) {
      notifyBad(errorMessage(error, '更新特别关心失败'))
    } finally {
      setBusyId(null)
    }
  }

  function accountActions(account: Account, includeModels: boolean): AccountAction[] {
    return [
      {
        key: 'detail',
        label: '详情',
        icon: ListTree,
        onSelect: () => navigate(`/accounts/${account.id}`),
      },
      ...(canProbe
        ? [{ key: 'probe', label: '探测', icon: Activity, onSelect: () => void runProbe(account.id) }]
        : []),
      ...(canQuota && providers.find((item) => item.id === account.provider)?.supports_quota !== false
        ? [{ key: 'quota', label: '刷新额度', icon: Coins, onSelect: () => void runQuota(account.id) }]
        : []),
      ...(includeModels && canModels
        ? [{ key: 'models', label: '获取模型', icon: ListTree, onSelect: () => void runModels(account.id) }]
        : []),
      ...(account.auth_type === 'oauth' && canUpdate
        ? [{ key: 'oauth', label: '去授权', icon: KeyRound, onSelect: () => void startOauth(account.id) }]
        : []),
      ...(canUpdate ? [{ key: 'edit', label: '编辑', icon: Pencil, onSelect: () => setEditor(account) }] : []),
      ...(canUpdate
        ? [
            {
              key: 'toggle',
              label: account.status === 'active' ? '停用' : '启用',
              icon: Power,
              onSelect: () => void toggle(account),
            },
          ]
        : []),
      ...(canDelete
        ? [{ key: 'delete', label: '删除', icon: Trash2, danger: true, onSelect: () => setDeletingAccount(account) }]
        : []),
    ]
  }

  const selectedAccount =
    selectedId != null && Number.isFinite(selectedId)
      ? accounts.find((account) => account.id === selectedId) ?? null
      : null
  if (selectedId != null) {
    return (
      <>
        <AccountDetailPage
          account={selectedAccount}
          providers={providers}
          supportsQuota={
            selectedAccount
              ? providers.find((item) => item.id === selectedAccount.provider)?.supports_quota !== false
              : false
          }
          loading={!accounts.length}
          busy={busyId === selectedAccount?.id}
          canUpdate={canUpdate}
          actions={selectedAccount ? accountActions(selectedAccount, true) : []}
          expanded={selectedAccount ? expandedModels.has(selectedAccount.id) : false}
          onBack={() => navigate('/accounts')}
          onTogglePinned={() => {
            if (selectedAccount) void togglePinned(selectedAccount)
          }}
          onToggleModels={() => {
            if (!selectedAccount) return
            setExpandedModels((current) => {
              const next = new Set(current)
              if (next.has(selectedAccount.id)) next.delete(selectedAccount.id)
              else next.add(selectedAccount.id)
              return next
            })
          }}
          onAddModel={() => {
            if (selectedAccount) setCustomModelAccountId(selectedAccount.id)
          }}
          onEditModel={(model) => {
            if (selectedAccount) setEditingModel({ accountId: selectedAccount.id, model })
          }}
          onToggleModel={(model) => {
            if (selectedAccount) void toggleModelEnabled(selectedAccount.id, model)
          }}
        />
        <AccountDialogs
          providers={providers}
          editor={editor}
          setEditor={setEditor}
          transfer={transfer}
          setTransfer={setTransfer}
          editingModel={editingModel}
          setEditingModel={setEditingModel}
          customModelAccountId={customModelAccountId}
          setCustomModelAccountId={setCustomModelAccountId}
          deletingAccount={deletingAccount}
          setDeletingAccount={setDeletingAccount}
          oauthDialog={oauthDialog}
          setOauthDialog={setOauthDialog}
          oauthPaste={oauthPaste}
          setOauthPaste={setOauthPaste}
          oauthAccountId={oauthAccountId}
          setOauthAccountId={setOauthAccountId}
          queryClient={queryClient}
        />
      </>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold">上游账号</h1>
            <p className="mt-1 text-sm text-mist">
              预设 OpenCode Go、Grok、DeepSeek，也可选通用 OpenAI / Anthropic。探测只在你点的时候发生。
            </p>
          </div>
<Can code="accounts:create">
          <Button type="button" className="shrink-0 lg:hidden" onClick={() => setEditor('new')}>
            新建账号
          </Button>
</Can>
        </div>
        <div className="grid grid-cols-2 items-end gap-2 lg:flex lg:shrink-0 lg:flex-nowrap">
<Can code="accounts:export">
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setTransfer('export')}>
            导出
          </Button>
</Can>
<Can code="accounts:import">
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setTransfer('import')}>
            导入
          </Button>
</Can>
<Can code="accounts:create">
          <Button type="button" className="hidden lg:inline-flex" onClick={() => setEditor('new')}>
            新建账号
          </Button>
</Can>
        </div>
      </div>
      <FilterBar
        activeCount={[search, statusFilter, providerFilter].filter(Boolean).length}
        onReset={() => {
          setSearch('')
          setStatusFilter('')
          setProviderFilter('')
        }}
        desktopClassName="grid grid-cols-2 gap-3 p-3 lg:grid-cols-3"
      >
        <div className="col-span-2 min-w-0 lg:col-span-1">
          <Field label="搜索">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索名称 / 供应商 / URL"
            />
          </Field>
        </div>
        <Field label="状态">
          <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </Select>
        </Field>
        <Field label="供应商">
          <Select value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}>
            <option value="">全部供应商</option>
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
      </FilterBar>
      <InfoMasonry
        items={filteredAccounts}
        itemKey={(account) => String(account.id)}
        baseColumns={1}
        tabletColumns={2}
        desktopColumns={3}
        estimateHeight={(account) => estimateAccountCardHeight(account, providers)}
        renderItem={(account) => (
          <Card className="flex min-w-0 flex-col space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="truncate text-lg font-medium" title={account.name}>
                  {account.name}
                </div>
                <div className="truncate font-mono text-xs text-mist" title={`${account.provider} · ${account.base_url}`}>
                  {account.provider} · {account.base_url}
                </div>
                {account.website_url ? (
                  <a
                    className="mt-1 inline-flex max-w-full items-center gap-1 text-xs text-signal hover:underline"
                    href={account.website_url}
                    target="_blank"
                    rel="noreferrer"
                    title={account.website_url}
                  >
                    <span className="truncate">官网</span>
                    <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                  </a>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {canUpdate ? (
                  <PinButton
                    pinned={account.pinned}
                    disabled={busyId === account.id}
                    onToggle={() => void togglePinned(account)}
                  />
                ) : null}
                <Badge tone={account.status === 'active' ? 'ok' : 'mist'}>
                  {account.status === 'active' ? '启用' : '停用'}
                </Badge>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge
                tone={RISK_LEVELS[account.risk_level]?.tone ?? 'mist'}
                title={`上游账号风险：${RISK_LEVELS[account.risk_level]?.hint ?? ''}`}
              >
                {RISK_LEVELS[account.risk_level]?.label ?? account.risk_level}
              </Badge>
              <Badge tone={account.has_credential ? 'info' : 'warn'}>
                {account.has_credential ? '已配置凭证' : '缺凭证'}
              </Badge>
              {account.header_spoof && account.header_spoof !== 'none' ? (
                <Badge tone="info">{HEADER_SPOOF_LABELS[account.header_spoof] || '已伪装'}</Badge>
              ) : null}
              {account.last_probe_ok === true ? <Badge tone="ok">探测正常</Badge> : null}
              {account.last_probe_ok === false ? <Badge tone="bad">探测失败</Badge> : null}
            </div>
            <AccountActionRow actions={accountActions(account, false)} disabled={busyId === account.id} />
            {providers.find((item) => item.id === account.provider)?.supports_quota !== false ? (
              <div className="border-t border-line pt-3">
                <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">
                  额度{account.quota_updated_at ? ` · ${formatTime(account.quota_updated_at)}` : ''}
                </div>
                {account.quota?.items?.length ? (
                  <QuotaItems items={account.quota.items} />
                ) : (
                  <div className="text-sm text-mist">{account.quota?.message || '还没有额度，点「刷新额度」拉取。'}</div>
                )}
              </div>
            ) : null}
          </Card>
        )}
      />
      {filteredAccounts.length === 0 ? (
        <Card className="px-4 py-10 text-center text-sm text-mist">暂无匹配账号</Card>
      ) : null}
      <AccountDialogs
        providers={providers}
        editor={editor}
        setEditor={setEditor}
        transfer={transfer}
        setTransfer={setTransfer}
        editingModel={editingModel}
        setEditingModel={setEditingModel}
        customModelAccountId={customModelAccountId}
        setCustomModelAccountId={setCustomModelAccountId}
        deletingAccount={deletingAccount}
        setDeletingAccount={setDeletingAccount}
        oauthDialog={oauthDialog}
        setOauthDialog={setOauthDialog}
        oauthPaste={oauthPaste}
        setOauthPaste={setOauthPaste}
        oauthAccountId={oauthAccountId}
        setOauthAccountId={setOauthAccountId}
        queryClient={queryClient}
      />
    </div>
  )
}

function AccountDialogs({
  providers,
  editor,
  setEditor,
  transfer,
  setTransfer,
  editingModel,
  setEditingModel,
  customModelAccountId,
  setCustomModelAccountId,
  deletingAccount,
  setDeletingAccount,
  oauthDialog,
  setOauthDialog,
  oauthPaste,
  setOauthPaste,
  oauthAccountId,
  setOauthAccountId,
  queryClient,
}: {
  providers: Provider[]
  editor: Account | 'new' | null
  setEditor: (value: Account | 'new' | null) => void
  transfer: 'export' | 'import' | null
  setTransfer: (value: 'export' | 'import' | null) => void
  editingModel: { accountId: number; model: ModelCaps } | null
  setEditingModel: (value: { accountId: number; model: ModelCaps } | null) => void
  customModelAccountId: number | null
  setCustomModelAccountId: (value: number | null) => void
  deletingAccount: Account | null
  setDeletingAccount: (value: Account | null) => void
  oauthDialog: boolean
  setOauthDialog: (value: boolean) => void
  oauthPaste: string
  setOauthPaste: (value: string) => void
  oauthAccountId: number | null
  setOauthAccountId: (value: number | null) => void
  queryClient: ReturnType<typeof useQueryClient>
}) {
  return (
    <>
      {transfer === 'export' ? <ExportDialog onClose={() => setTransfer(null)} /> : null}
      {transfer === 'import' ? (
        <ImportDialog
          onClose={() => setTransfer(null)}
          onImported={() => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
          }}
        />
      ) : null}
      {editingModel ? (
        <ModelOverrideDialog
          accountId={editingModel.accountId}
          model={editingModel.model}
          onClose={() => setEditingModel(null)}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
            setEditingModel(null)
          }}
        />
      ) : null}
      {customModelAccountId !== null ? (
        <CustomModelDialog
          accountId={customModelAccountId}
          onClose={() => setCustomModelAccountId(null)}
          onSaved={(created) => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
            setCustomModelAccountId(null)
            notifyOk(created ? '模型已添加' : '该模型已在列表中')
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
      {deletingAccount ? (
        <DeleteAccountDialog
          account={deletingAccount}
          onClose={() => setDeletingAccount(null)}
          onDeleted={() => {
            queryClient.invalidateQueries({ queryKey: ['accounts'] })
            queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
            queryClient.invalidateQueries({ queryKey: ['skill-classification-settings'] })
            setDeletingAccount(null)
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
                    notifyBad(errorMessage(error, '兑换授权失败'))
                  }
                }}
              >
                完成授权
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}

const AUTH_TYPE_LABELS: Record<string, string> = {
  api_key: 'API Key',
  oauth: 'OAuth',
}

function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-line/70 py-2.5 last:border-b-0 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:items-start sm:gap-3">
      <div className="text-xs text-mist">{label}</div>
      <div className="min-w-0 break-words text-sm text-paper">{children}</div>
    </div>
  )
}

function AccountDetailPage({
  account,
  providers,
  supportsQuota,
  loading,
  busy,
  canUpdate,
  actions,
  expanded,
  onBack,
  onTogglePinned,
  onToggleModels,
  onAddModel,
  onEditModel,
  onToggleModel,
}: {
  account: Account | null
  providers: Provider[]
  supportsQuota: boolean
  loading: boolean
  busy: boolean
  canUpdate: boolean
  actions: AccountAction[]
  expanded: boolean
  onBack: () => void
  onTogglePinned: () => void
  onToggleModels: () => void
  onAddModel: () => void
  onEditModel: (model: ModelCaps) => void
  onToggleModel: (model: ModelCaps) => void
}) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button type="button" variant="ghost" size="sm" className="px-0" onClick={onBack}>
          返回账号列表
        </Button>
        {account && canUpdate ? (
          <PinButton pinned={account.pinned} disabled={busy} onToggle={onTogglePinned} />
        ) : null}
      </div>
      {loading && !account ? <Card className="px-4 py-10 text-center text-sm text-mist">正在读取账号</Card> : null}
      {!loading && !account ? <Card className="px-4 py-10 text-center text-sm text-mist">账号不存在或已删除</Card> : null}
      {account ? (
        <>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <h1 className="truncate text-2xl font-semibold" title={account.name}>
                {account.name}
              </h1>
              <div className="mt-1 truncate font-mono text-xs text-mist" title={account.base_url}>
                {account.provider} · {account.base_url}
              </div>
            </div>
            <AccountActionRow actions={actions} disabled={busy} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge tone={account.status === 'active' ? 'ok' : 'mist'}>
              {account.status === 'active' ? '启用' : '停用'}
            </Badge>
            <Badge
              tone={RISK_LEVELS[account.risk_level]?.tone ?? 'mist'}
              title={`上游账号风险：${RISK_LEVELS[account.risk_level]?.hint ?? ''}`}
            >
              {RISK_LEVELS[account.risk_level]?.label ?? account.risk_level}
            </Badge>
            <Badge tone={account.has_credential ? 'info' : 'warn'}>
              {account.has_credential ? '已配置凭证' : '缺凭证'}
            </Badge>
            {account.header_spoof && account.header_spoof !== 'none' ? (
              <Badge tone="info">{HEADER_SPOOF_LABELS[account.header_spoof] || '已伪装'}</Badge>
            ) : null}
            {account.last_probe_ok === true ? <Badge tone="ok">探测正常</Badge> : null}
            {account.last_probe_ok === false ? <Badge tone="bad">探测失败</Badge> : null}
            {account.pinned ? <Badge tone="info">特别关心</Badge> : null}
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            <Card>
              <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">账号信息</div>
              <DetailRow label="显示名">{account.name}</DetailRow>
              <DetailRow label="供应商">
                {providers.find((item) => item.id === account.provider)?.label ?? account.provider}
                <span className="ml-2 font-mono text-xs text-mist">{account.provider}</span>
              </DetailRow>
              <DetailRow label="来源">{account.source === 'agent' ? '网关代理' : '上游账号'}</DetailRow>
              <DetailRow label="鉴权">{AUTH_TYPE_LABELS[account.auth_type] ?? account.auth_type}</DetailRow>
              <DetailRow label="状态">{account.status === 'active' ? '启用' : '停用'}</DetailRow>
              <DetailRow label="风险">
                {RISK_LEVELS[account.risk_level]?.label ?? account.risk_level}
                <div className="mt-0.5 text-xs text-mist">{RISK_LEVELS[account.risk_level]?.hint}</div>
              </DetailRow>
              <DetailRow label="特别关心">{account.pinned ? '已置顶' : '未标记'}</DetailRow>
              <DetailRow label="创建时间">{formatTime(account.created_at)}</DetailRow>
            </Card>
            <Card>
              <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">连接与凭证</div>
              <DetailRow label="Base URL">
                <span className="font-mono text-xs">{account.base_url}</span>
              </DetailRow>
              <DetailRow label="官网">
                {account.website_url ? (
                  <a
                    className="inline-flex max-w-full items-center gap-1 text-signal hover:underline"
                    href={account.website_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="truncate">{account.website_url}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                ) : (
                  <span className="text-mist">未填写</span>
                )}
              </DetailRow>
              <DetailRow label="凭证">{account.has_credential ? '已配置' : '缺少凭证'}</DetailRow>
              <DetailRow label="请求头伪装">
                {account.header_spoof && account.header_spoof !== 'none'
                  ? HEADER_SPOOF_LABELS[account.header_spoof] || account.header_spoof
                  : '关闭'}
              </DetailRow>
              <DetailRow label="模型前缀">
                <span className="font-mono text-xs">{account.model_prefix || '未设置'}</span>
              </DetailRow>
              {account.agent_route_id ? (
                <DetailRow label="代理路由">
                  <span className="font-mono text-xs">{account.agent_route_id}</span>
                </DetailRow>
              ) : null}
              {account.auth_type === 'oauth' ? (
                <DetailRow label="授权到期">
                  {account.oauth_expires_at ? formatTime(account.oauth_expires_at) : '未授权或不会过期'}
                </DetailRow>
              ) : null}
            </Card>
            <Card>
              <div className="mb-1 text-xs uppercase tracking-[0.16em] text-mist">探测</div>
              <DetailRow label="结果">
                {account.last_probe_ok == null ? '还没有探测' : account.last_probe_ok ? '正常' : '失败'}
              </DetailRow>
              <DetailRow label="耗时">
                {account.last_probe_latency_ms != null ? `${account.last_probe_latency_ms}ms` : '无'}
              </DetailRow>
              <DetailRow label="时间">{formatTime(account.last_probe_at)}</DetailRow>
              <DetailRow label="返回">
                <span className="whitespace-pre-wrap font-mono text-xs">
                  {account.last_probe_message?.trim() || '无'}
                </span>
              </DetailRow>
            </Card>
            {supportsQuota ? (
              <Card>
                <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">
                  额度{account.quota_updated_at ? ` · ${formatTime(account.quota_updated_at)}` : ''}
                </div>
                {account.quota?.items?.length ? (
                  <QuotaItems items={account.quota.items} />
                ) : (
                  <div className="text-sm text-mist">{account.quota?.message || '还没有额度，点「刷新额度」拉取。'}</div>
                )}
              </Card>
            ) : null}
          </div>
          <Card className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs uppercase tracking-[0.16em] text-mist">
                  模型{account.models_updated_at ? ` · ${formatTime(account.models_updated_at)}` : ''}
                </div>
                <Can code="accounts:models_manage">
                  <Button type="button" variant="line" size="sm" onClick={onAddModel}>
                    添加模型
                  </Button>
                </Can>
              </div>
              {account.models?.length ? (
                <ModelList
                  models={account.models}
                  expanded={expanded}
                  onToggle={onToggleModels}
                  onEdit={onEditModel}
                  onToggleEnabled={onToggleModel}
                />
              ) : (
                <div className="text-sm text-mist">还没有模型，点「获取模型」从上游拉取并入库。</div>
              )}
            </Card>
        </>
      ) : null}
    </div>
  )
}
