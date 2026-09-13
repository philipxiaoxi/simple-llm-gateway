import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Copy, Globe, LayoutGrid, Plug, ScanText, Settings2, Sparkles } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Select, Switch } from '../components/ui'
import { api, type AppBindingAccount, type AppItem } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, errorMessage, formatTime } from '../lib/utils'

const iconMap = {
  'scan-text': ScanText,
  globe: Globe,
} as const

function AppIcon({ name }: { name: string }) {
  const Icon = iconMap[name as keyof typeof iconMap] ?? LayoutGrid
  return <Icon size={22} className="text-signal" />
}

function ConfigDialog({
  app,
  accounts,
  onClose,
}: {
  app: AppItem
  accounts: AppBindingAccount[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const needsBinding = app.capabilities.includes('vision') || app.capabilities.includes('image_input')
  const [enabled, setEnabled] = useState(app.enabled)
  const [accountId, setAccountId] = useState(app.bound_account_id ? String(app.bound_account_id) : '')
  const [model, setModel] = useState(app.bound_model || '')
  const [systemPrompt, setSystemPrompt] = useState(String(app.config.system_prompt || ''))
  const [maxTokens, setMaxTokens] = useState(String(app.config.max_tokens ?? 2048))
  const [maxSites, setMaxSites] = useState(String(app.config.max_sites ?? 20))
  const [maxSiteMb, setMaxSiteMb] = useState(String(Math.round(Number(app.config.max_site_bytes || 0) / (1024 * 1024)) || 50))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  const selected = accounts.find((item) => String(item.id) === accountId)
  const models = selected?.models?.length ? selected.models : selected?.default_model ? [selected.default_model] : []

  async function save() {
    setPending(true)
    setError('')
    try {
      const config: Record<string, unknown> = { ...app.config }
      if (app.id === 'ocr') {
        config.system_prompt = systemPrompt
        config.max_tokens = Number(maxTokens) || 2048
      }
      if (app.id === 'static-deploy') {
        config.max_sites = Number(maxSites) || 20
        config.max_site_bytes = (Number(maxSiteMb) || 50) * 1024 * 1024
      }
      const payload: Parameters<typeof api.updateApp>[1] = {
        enabled,
        config,
      }
      if (needsBinding) {
        if (!accountId) {
          payload.clear_binding = true
          payload.bound_account_id = null
          payload.bound_model = null
        } else {
          payload.bound_account_id = Number(accountId)
          payload.bound_model = model || selected?.default_model || null
        }
      }
      await api.updateApp(app.id, payload)
      await queryClient.invalidateQueries({ queryKey: ['apps'] })
      notifyOk('应用配置已保存')
      onClose()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
      notifyBad(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title={`配置 · ${app.name}`} onClose={onClose} className="max-w-xl">
      <div className="grid gap-4">
        <div className="flex items-center justify-between gap-3 rounded-lg border border-line bg-white/[0.02] px-3 py-2">
          <div>
            <div className="text-sm text-paper">启用应用</div>
            <div className="text-xs text-mist">关闭后入口仍可见，但不可调用能力</div>
          </div>
          <Switch checked={enabled} onCheckedChange={setEnabled} />
        </div>

        {needsBinding ? (
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="上游账号">
              <Select value={accountId} onChange={(event) => {
                setAccountId(event.target.value)
                const next = accounts.find((item) => String(item.id) === event.target.value)
                setModel(next?.default_model || '')
              }}>
                <option value="">请选择账号</option>
                {accounts.map((item) => (
                  <option key={item.id} value={item.id} disabled={!item.available}>
                    {item.name} ({item.provider}){item.available ? '' : ' · 不可用'}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="模型">
              <Select value={model} onChange={(event) => setModel(event.target.value)} disabled={!accountId}>
                <option value="">使用账号默认模型</option>
                {models.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        ) : null}

        {app.id === 'ocr' ? (
          <>
            <Field label="系统提示词">
              <textarea
                className="min-h-28 w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none focus:border-signal/70"
                value={systemPrompt}
                onChange={(event) => setSystemPrompt(event.target.value)}
              />
            </Field>
            <Field label="最大输出 Token">
              <Input value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} inputMode="numeric" />
            </Field>
          </>
        ) : null}

        {app.id === 'static-deploy' ? (
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="站点数量上限">
              <Input value={maxSites} onChange={(event) => setMaxSites(event.target.value)} inputMode="numeric" />
            </Field>
            <Field label="单站体积上限 (MB)">
              <Input value={maxSiteMb} onChange={(event) => setMaxSiteMb(event.target.value)} inputMode="numeric" />
            </Field>
          </div>
        ) : null}

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

export function AppsPage() {
  const queryClient = useQueryClient()
  const { data: apps = [], isLoading } = useQuery({ queryKey: ['apps'], queryFn: api.apps })
  const { data: accounts = [] } = useQuery({ queryKey: ['app-binding-accounts'], queryFn: api.appBindingAccounts })
  const { data: integration } = useQuery({ queryKey: ['apps-integration'], queryFn: api.appIntegration })
  const [configApp, setConfigApp] = useState<AppItem | null>(null)

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => api.updateApp(id, { enabled }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['apps'] })
      await queryClient.invalidateQueries({ queryKey: ['apps-integration'] })
      notifyOk('已更新启用状态')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  const grouped = useMemo(() => {
    const map = new Map<string, AppItem[]>()
    for (const app of apps) {
      const list = map.get(app.category) || []
      list.push(app)
      map.set(app.category, list)
    }
    return [...map.entries()]
  }, [apps])

  const cursorConfigText = integration
    ? JSON.stringify(integration.cursor_config, null, 2)
    : ''

  return (
    <div className="grid gap-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-paper">应用中心</h1>
          <p className="mt-1 text-sm text-mist">
            内置可插拔应用；对 AI 暴露 MCP 工具与 Skills，可用 API Key 直接调用。
          </p>
        </div>
        <Badge tone="mist">{apps.length} 个应用</Badge>
      </div>

      {integration ? (
        <Card className="grid gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-base font-medium text-paper">
                <Plug size={18} className="text-signal" /> AI 调用入口（MCP / Skills）
              </div>
              <p className="mt-1 text-sm text-mist">
                {integration.server.description} · 鉴权：{integration.auth_header}
              </p>
            </div>
            <Link to="/skills">
              <Button type="button" variant="line">
                <Sparkles size={16} /> 去 Skills 安装
              </Button>
            </Link>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="MCP URL">
              <div className="flex gap-2">
                <Input value={integration.mcp_url} readOnly />
                <Button
                  type="button"
                  variant="line"
                  onClick={() => {
                    void copyText(integration.mcp_url).then((ok) =>
                      ok ? notifyOk('已复制 MCP URL') : notifyBad('复制失败'),
                    )
                  }}
                >
                  <Copy size={16} />
                </Button>
              </div>
            </Field>
            <Field label="REST tools">
              <div className="flex gap-2">
                <Input value={integration.rest_tools_url} readOnly />
                <Button
                  type="button"
                  variant="line"
                  onClick={() => {
                    void copyText(integration.rest_tools_url).then((ok) =>
                      ok ? notifyOk('已复制') : notifyBad('复制失败'),
                    )
                  }}
                >
                  <Copy size={16} />
                </Button>
              </div>
            </Field>
          </div>
          <Field label="Cursor / 客户端 MCP 配置示例">
            <textarea
              className="min-h-36 w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs text-paper outline-none focus:border-signal/70"
              readOnly
              value={cursorConfigText}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="line"
              onClick={() => {
                void copyText(cursorConfigText).then((ok) =>
                  ok ? notifyOk('已复制 MCP 配置') : notifyBad('复制失败'),
                )
              }}
            >
              <Copy size={16} /> 复制 MCP 配置
            </Button>
            {integration.skill_slugs.map((slug) => (
              <Badge key={slug} tone="mist">
                Skill: {slug}
              </Badge>
            ))}
          </div>
          <div className="grid gap-2">
            <div className="text-xs uppercase tracking-[0.16em] text-mist">当前暴露工具</div>
            <div className="flex flex-wrap gap-2">
              {integration.tools.length ? (
                integration.tools.map((tool) => (
                  <Badge key={tool.name} tone="ok" title={tool.description}>
                    {tool.name}
                  </Badge>
                ))
              ) : (
                <span className="text-sm text-mist">暂无（请先启用至少一个应用）</span>
              )}
            </div>
          </div>
          <ul className="grid gap-1 text-sm text-mist">
            {integration.notes.map((note) => (
              <li key={note}>· {note}</li>
            ))}
          </ul>
        </Card>
      ) : null}

      {isLoading ? <Card className="text-sm text-mist">加载中…</Card> : null}

      {grouped.map(([category, items]) => (
        <section key={category} className="grid gap-3">
          <h2 className="text-sm font-medium text-mist">{category}</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((app) => (
              <Card key={app.id} className="flex h-full flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3">
                    <div className="rounded-lg border border-line bg-white/[0.03] p-2.5">
                      <AppIcon name={app.icon} />
                    </div>
                    <div>
                      <div className="font-medium text-paper">{app.name}</div>
                      <div className="mt-1 text-xs text-mist">v{app.version}</div>
                    </div>
                  </div>
                  <Switch
                    checked={app.enabled}
                    disabled={toggle.isPending}
                    onCheckedChange={(checked) => toggle.mutate({ id: app.id, enabled: checked })}
                  />
                </div>
                <p className="flex-1 text-sm leading-6 text-mist">{app.description}</p>
                <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
                  <Badge tone={app.enabled ? 'ok' : 'mist'}>{app.enabled ? '已启用' : '已禁用'}</Badge>
                  {app.bound_account_id ? <Badge tone="mist">已绑定账号 #{app.bound_account_id}</Badge> : null}
                  {app.updated_at ? <span>更新于 {formatTime(app.updated_at)}</span> : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="line" onClick={() => setConfigApp(app)}>
                    <Settings2 size={16} /> 配置
                  </Button>
                  <Link to={app.entry_path} className="inline-flex">
                    <Button type="button" disabled={!app.enabled}>
                      进入 <ArrowRight size={16} />
                    </Button>
                  </Link>
                </div>
              </Card>
            ))}
          </div>
        </section>
      ))}

      {configApp ? <ConfigDialog app={configApp} accounts={accounts} onClose={() => setConfigApp(null)} /> : null}
    </div>
  )
}
