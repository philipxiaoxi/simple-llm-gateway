import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Copy, KeyRound, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ItemPickDialog } from '../components/ItemPickDialog'
import { Badge, Button, Card, Select } from '../components/ui'
import { api, type McpIntegrationCapability } from '../lib/api'
import { useCan } from '../lib/session'
import {
  buildCapabilitySkill,
  buildMcpClientConfig,
  buildMcpPrompt,
  buildRestPrompt,
  toolParams,
  type IntegrationInput,
  type IntegrationRestEndpoint,
  type IntegrationTool,
  type ToolParamRow,
} from '../lib/mcpIntegration'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, copyText, errorMessage, formatTime } from '../lib/utils'

const METHODS = [
  { id: 'mcp', label: 'MCP', desc: '支持 MCP 的客户端，直接接入工具' },
  { id: 'skill', label: 'Skill', desc: '把提示词交给 AI，用 HTTP 调用' },
] as const

type MethodId = (typeof METHODS)[number]['id']

function methodTone(method: string) {
  switch (method.toUpperCase()) {
    case 'GET':
      return 'text-info'
    case 'POST':
      return 'text-signal'
    case 'PUT':
    case 'PATCH':
      return 'text-warn'
    case 'DELETE':
      return 'text-danger'
    default:
      return 'text-mist'
  }
}

function ParamTable({ params }: { params: ToolParamRow[] }) {
  if (!params.length) return <p className="text-xs text-mist">无请求参数</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead className="border-b border-line bg-panel-2 text-xs text-mist">
          <tr>
            <th className="px-3 py-2 font-medium">参数</th>
            <th className="px-3 py-2 font-medium">类型</th>
            <th className="px-3 py-2 font-medium">必填</th>
            <th className="px-3 py-2 font-medium">说明</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {params.map((param) => (
            <tr key={param.name}>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-paper">{param.name}</td>
              <td className="px-3 py-2 font-mono text-xs text-info [overflow-wrap:anywhere]">{param.type}</td>
              <td className="whitespace-nowrap px-3 py-2 text-xs">
                {param.required ? <Badge tone="warn">必填</Badge> : <span className="text-mist">可选</span>}
              </td>
              <td className="px-3 py-2 text-mist [overflow-wrap:anywhere]">{param.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Disclosure({
  title,
  subtitle,
  badges,
  actions,
  className,
  bodyClassName,
  children,
}: {
  title: ReactNode
  subtitle?: ReactNode
  badges?: ReactNode
  actions?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className={cn('overflow-hidden rounded-xl border border-line bg-ink/40', className)}>
      <div className="flex items-start gap-2 px-3 py-2.5">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          <ChevronDown
            size={16}
            className={cn('mt-0.5 shrink-0 text-mist transition-transform', open && 'rotate-180')}
          />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              {title}
              {badges}
            </span>
            {subtitle ? (
              <span className="mt-1 block text-xs text-mist [overflow-wrap:anywhere]">{subtitle}</span>
            ) : null}
          </span>
        </button>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>
      {open ? <div className={cn('space-y-3 border-t border-line p-3', bodyClassName)}>{children}</div> : null}
    </div>
  )
}

function ToolDoc({ tool, capabilityId }: { tool: IntegrationTool; capabilityId?: string }) {
  return (
    <Disclosure
      className="bg-panel/40"
      title={<code className="font-mono text-sm text-paper [overflow-wrap:anywhere]">{tool.name}</code>}
      badges={
        <>
          {capabilityId ? <Badge tone="mist">{capabilityId}</Badge> : null}
          {tool.operation && tool.operation !== tool.name ? <Badge tone="mist">{tool.operation}</Badge> : null}
        </>
      }
      subtitle={tool.description || undefined}
    >
      <div>
        <div className="mb-1 text-xs uppercase tracking-[0.12em] text-mist">请求参数</div>
        <ParamTable params={toolParams(tool)} />
      </div>
      {tool.returns ? (
        <div>
          <div className="mb-1 text-xs uppercase tracking-[0.12em] text-mist">返回</div>
          <p className="text-sm text-mist [overflow-wrap:anywhere]">{tool.returns}</p>
        </div>
      ) : null}
    </Disclosure>
  )
}

function EndpointTable({ endpoints }: { endpoints: IntegrationRestEndpoint[] }) {
  if (!endpoints.length) return null
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[520px] text-left text-sm">
        <thead className="border-b border-line bg-panel-2 text-xs text-mist">
          <tr>
            <th className="px-3 py-2 font-medium">方法</th>
            <th className="px-3 py-2 font-medium">路径</th>
            <th className="px-3 py-2 font-medium">说明</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {endpoints.map((endpoint) => (
            <tr key={`${endpoint.method}${endpoint.path}`}>
              <td className={cn('whitespace-nowrap px-3 py-2 font-mono text-xs', methodTone(endpoint.method))}>
                {endpoint.method}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-paper [overflow-wrap:anywhere]">{endpoint.path}</td>
              <td className="px-3 py-2 text-mist">
                {endpoint.summary || '—'}
                {endpoint.content_type ? (
                  <span className="ml-1 text-xs text-mist-soft">· {endpoint.content_type}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CapabilityDoc({
  capability,
  onCopy,
}: {
  capability: McpIntegrationCapability
  onCopy?: () => void
}) {
  const endpoints = capability.integration?.rest_endpoints || []
  const notes = capability.integration?.notes || []
  return (
    <Disclosure
      className="bg-panel/50"
      title={<span className="text-sm font-medium text-paper">{capability.name}</span>}
      badges={<Badge tone="mist">{capability.capability_id}</Badge>}
      subtitle={capability.description || undefined}
      actions={
        onCopy ? (
          <Button type="button" variant="line" className="px-2 py-1 text-xs" onClick={onCopy}>
            <Copy size={12} /> 复制 Skill
          </Button>
        ) : null
      }
    >
      {endpoints.length ? (
        <Disclosure
          className="bg-ink/40"
          title={<span className="text-xs font-medium uppercase tracking-[0.12em] text-mist">REST 接口</span>}
          bodyClassName="p-0"
        >
          <EndpointTable endpoints={endpoints} />
        </Disclosure>
      ) : null}

      {capability.tools.length ? (
        <div className="space-y-2">
          {capability.tools.map((tool) => (
            <ToolDoc key={tool.name} tool={tool} />
          ))}
        </div>
      ) : null}

      {notes.length ? (
        <div>
          <div className="mb-1 text-xs uppercase tracking-[0.12em] text-mist">约束</div>
          <ul className="space-y-1 text-xs text-mist">
            {notes.map((note, index) => (
              <li key={index} className="flex gap-2">
                <span className="text-signal">•</span>
                <span className="[overflow-wrap:anywhere]">{note}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Disclosure>
  )
}

function ConnInfo({ rows }: { rows: [string, string][] }) {
  return (
    <div className="grid gap-x-4 gap-y-2 rounded-lg border border-line bg-ink/40 p-3 text-sm sm:grid-cols-[7rem_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <span className="text-mist">{label}</span>
          <span className="break-all font-mono text-xs text-paper [overflow-wrap:anywhere]">{value}</span>
        </div>
      ))}
    </div>
  )
}

export function McpDocsPage() {
  const [params, setParams] = useSearchParams()
  const queryKeyId = params.get('key')
  const [method, setMethod] = useState<MethodId>('mcp')
  const [dialog, setDialog] = useState<null | MethodId>(null)

  const canListKeys = useCan('mcp_keys:view')
  const canReveal = useCan('mcp_keys:reveal')
  const keys = useQuery({ queryKey: ['mcp-keys'], queryFn: () => api.mcpKeys(), enabled: canListKeys })
  const keyId = useMemo(() => {
    if (queryKeyId) return Number(queryKeyId)
    return keys.data?.[0]?.id
  }, [queryKeyId, keys.data])

  useEffect(() => {
    if (!queryKeyId && keys.data?.length) {
      const next = new URLSearchParams(params)
      next.set('key', String(keys.data[0].id))
      setParams(next, { replace: true })
    }
  }, [queryKeyId, keys.data, params, setParams])

  const integration = useQuery({
    queryKey: ['mcp-key-integration', keyId],
    queryFn: () => api.mcpKeyIntegration(keyId as number),
    enabled: Boolean(keyId),
  })
  const reveal = useQuery({
    queryKey: ['mcp-key-reveal', keyId],
    queryFn: () => api.revealMcpKey(keyId as number),
    enabled: canReveal && Boolean(keyId),
    staleTime: 5 * 60_000,
  })

  const data = integration.data
  const capabilities = data?.capabilities ?? []
  const authorized = capabilities.filter((capability) => capability.authorized)
  const keyValue = reveal.data?.key ?? ''
  const input: IntegrationInput | null =
    data && keyValue
      ? {
          keyName: data.key.name,
          keyValue,
          mcpUrl: data.mcp_url,
          restBaseUrl: data.rest_base_url,
          capabilities,
        }
      : null
  const toolCount = authorized.reduce((count, capability) => count + capability.tools.length, 0)

  async function copyTextOrNotify(text: string, label: string) {
    if (!text) return
    try {
      await copyText(text)
      notifyOk(`${label}已复制`)
    } catch (error) {
      notifyBad(errorMessage(error, '复制失败'))
    }
  }

  function selectKey(value: string) {
    const next = new URLSearchParams(params)
    next.set('key', value)
    setParams(next, { replace: true })
  }

  if (keys.isError) {
    return <div className="text-sm text-danger">{errorMessage(keys.error, '加载 MCP Key 失败')}</div>
  }
  if (!keys.isLoading && !keys.data?.length) {
    return (
      <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
        还没有 MCP Key。请先到「MCP Key」页创建一把，再回来生成接入配置。
      </div>
    )
  }

  const active = METHODS.find((item) => item.id === method) ?? METHODS[0]

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <h2 className="text-lg font-medium">接入中心</h2>
            <p className="mt-1 text-sm text-mist">按 Key 生成接入配置与文档，只包含该 Key 已授权的能力。</p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              className="min-w-0 flex-1 lg:w-72 lg:flex-none"
              value={keyId ? String(keyId) : ''}
              onChange={(event) => selectKey(event.target.value)}
            >
              {(keys.data || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.key_prefix} · {item.status}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              variant="ghost"
              className="shrink-0"
              title="刷新"
              onClick={() => {
                void integration.refetch()
                void reveal.refetch()
              }}
            >
              <RefreshCw size={15} />
            </Button>
          </div>
        </div>

        {data ? (
          <div className="space-y-3 border-t border-line pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={data.key.status === 'active' ? 'ok' : 'warn'}>{data.key.status}</Badge>
              <span className="text-xs text-mist">已授权 {authorized.length} 个能力 · {toolCount} 个工具</span>
              <Button
                type="button"
                variant="line"
                className="px-2 py-1 text-xs"
                disabled={!keyValue}
                onClick={() => void copyTextOrNotify(keyValue, '密钥')}
              >
                <KeyRound size={12} /> 复制密钥
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {capabilities.map((capability) => (
                <span
                  key={capability.capability_id}
                  title={capability.authorized ? capability.description : '该 Key 未授权此能力'}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs',
                    capability.authorized
                      ? 'border-signal/40 bg-signal/10 text-signal'
                      : 'border-line text-mist-soft',
                  )}
                >
                  {capability.capability_id}
                </span>
              ))}
            </div>
            {!keyValue ? (
              <div className="text-xs text-mist">
                {reveal.isError ? '密钥读取失败，请确认 APP_SECRET_KEY 未更换。' : '密钥读取中…'}
              </div>
            ) : null}
            {data.key.status !== 'active' ? (
              <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                该 Key 已停用，配置可用但调用会被拒绝。
              </div>
            ) : null}
          </div>
        ) : (
          <div className="text-sm text-mist">加载中…</div>
        )}
      </Card>

      {data ? (
        <div className="min-w-0 space-y-4">
          <div className="inline-flex rounded-lg border border-line bg-ink/40 p-1">
            {METHODS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setMethod(item.id)}
                className={cn(
                  'rounded-md px-4 py-2 text-sm font-medium transition',
                  method === item.id ? 'bg-signal/15 text-signal' : 'text-mist hover:text-paper',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <Card className="min-w-0 space-y-3 overflow-hidden p-4">
            <div>
              <h3 className="font-medium">{active.label} 接入</h3>
              <p className="mt-0.5 text-xs text-mist">{active.desc}</p>
            </div>

            {method === 'mcp' ? (
              <>
                <ConnInfo
                  rows={[
                    ['服务地址', data.mcp_url],
                    ['传输方式', 'Streamable HTTP'],
                    ['鉴权', 'Authorization: Bearer <密钥>（或 x-api-key）'],
                  ]}
                />
                <Disclosure
                  title={<span className="text-sm text-paper">客户端配置 JSON</span>}
                  actions={
                    <Button
                      type="button"
                      variant="line"
                      className="px-2 py-1 text-xs"
                      disabled={!input}
                      onClick={() => input && void copyTextOrNotify(buildMcpClientConfig(input, []), 'MCP 配置')}
                    >
                      <Copy size={12} /> 复制
                    </Button>
                  }
                >
                  <pre className="max-w-full min-w-0 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
                    {input ? buildMcpClientConfig(input, []) : '加载中…'}
                  </pre>
                </Disclosure>
                <div className="space-y-2">
                  <div className="text-xs uppercase tracking-[0.12em] text-mist">可用工具（{toolCount}）</div>
                  {authorized.flatMap((capability) =>
                    capability.tools.map((tool) => (
                      <ToolDoc
                        key={`${capability.capability_id}:${tool.name}`}
                        tool={tool}
                        capabilityId={capability.capability_id}
                      />
                    )),
                  )}
                  {!authorized.length ? <div className="text-sm text-mist">该 Key 还没有可用工具。</div> : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={!input}
                    onClick={() => input && void copyTextOrNotify(buildMcpClientConfig(input, []), 'MCP 配置')}
                  >
                    <Copy size={14} /> 复制 MCP 配置
                  </Button>
                  <Button type="button" variant="line" disabled={!input} onClick={() => setDialog('mcp')}>
                    复制为 AI 提示词
                  </Button>
                </div>
              </>
            ) : null}

            {method === 'skill' ? (
              <>
                <ConnInfo
                  rows={[
                    ['Base URL', data.rest_base_url],
                    ['鉴权', 'Authorization: Bearer <密钥>'],
                    ['请求体', 'application/json'],
                    ['错误体', '{"error":{"type","message"}}'],
                  ]}
                />
                <div className="space-y-2">
                  {authorized.map((capability) => (
                    <CapabilityDoc
                      key={capability.capability_id}
                      capability={capability}
                      onCopy={() =>
                        input &&
                        void copyTextOrNotify(buildCapabilitySkill(input, capability.capability_id), 'Skill 文档')
                      }
                    />
                  ))}
                  {!authorized.length ? <div className="text-sm text-mist">该 Key 没有可用能力。</div> : null}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="line" disabled={!input || !authorized.length} onClick={() => setDialog('skill')}>
                    复制为 AI 提示词
                  </Button>
                </div>
              </>
            ) : null}
          </Card>
        </div>
      ) : null}

      {dialog && input ? (
        <ItemPickDialog
          title={dialog === 'mcp' ? '复制 AI 提示词（MCP）' : '复制 AI 提示词（Skill）'}
          description="勾选要写入提示词的能力，未授权能力不会出现。"
          items={authorized.map((capability) => ({
            id: capability.capability_id,
            hint: `${capability.name} · ${capability.description}`,
          }))}
          defaultSelected={authorized.map((capability) => capability.capability_id)}
          unit="能力"
          confirmLabel="复制提示词"
          successMessage={(count) => `已复制 ${count} 个能力的 AI 提示词`}
          buildText={(selected) =>
            dialog === 'mcp' ? buildMcpPrompt(input, selected) : buildRestPrompt(input, selected)
          }
          onClose={() => setDialog(null)}
        />
      ) : null}

      {integration.isError ? (
        <div className="text-sm text-danger">{errorMessage(integration.error, '加载接入信息失败')}</div>
      ) : null}
      {keys.data?.length ? (
        <div className="text-xs text-mist">最近更新 {formatTime(keys.data[0].created_at)}</div>
      ) : null}
    </div>
  )
}

export default McpDocsPage
