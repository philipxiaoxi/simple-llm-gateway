import { useQuery } from '@tanstack/react-query'
import { Copy, KeyRound, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ItemPickDialog } from '../components/ItemPickDialog'
import { Badge, Button, Card, Select } from '../components/ui'
import { api, type McpIntegrationCapability } from '../lib/api'
import {
  buildCapabilityReference,
  buildMcpClientConfig,
  buildMcpPrompt,
  buildRestPrompt,
  type IntegrationInput,
} from '../lib/mcpIntegration'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, copyText, errorMessage, formatTime } from '../lib/utils'

const METHODS = [
  { id: 'mcp', label: 'MCP', desc: '支持 MCP 的客户端' },
  { id: 'rest', label: 'Skill + REST', desc: '提示词 + HTTP 调用' },
  { id: 'client', label: '客户端配置', desc: '通用 MCP JSON' },
  { id: 'reference', label: '快速参考', desc: '按能力查示例' },
] as const

type MethodId = (typeof METHODS)[number]['id']

function CapabilityChip({ capability }: { capability: McpIntegrationCapability }) {
  return (
    <span
      title={capability.authorized ? capability.description : '该 Key 未授权此能力'}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs',
        capability.authorized ? 'border-signal/40 bg-signal/10 text-signal' : 'border-line text-mist/60',
      )}
    >
      {capability.capability_id}
    </span>
  )
}

function ToolList({ capabilities }: { capabilities: McpIntegrationCapability[] }) {
  const tools = capabilities.flatMap((capability) =>
    capability.tools.map((tool) => ({ ...tool, capabilityId: capability.capability_id })),
  )
  if (!tools.length) return <div className="text-sm text-mist">该 Key 还没有可用工具。</div>
  return (
    <div className="space-y-1.5">
      {tools.map((tool) => (
        <div key={tool.name} className="rounded-md border border-line bg-ink/50 px-3 py-2">
          <div className="font-mono text-xs text-paper">{tool.name}</div>
          <div className="mt-0.5 break-words text-xs text-mist [overflow-wrap:anywhere]">{tool.description}</div>
        </div>
      ))}
    </div>
  )
}

export function McpDocsPage() {
  const [params, setParams] = useSearchParams()
  const queryKeyId = params.get('key')
  const [method, setMethod] = useState<MethodId>('mcp')
  const [dialog, setDialog] = useState<null | 'mcp' | 'rest'>(null)

  const keys = useQuery({ queryKey: ['mcp-keys'], queryFn: () => api.mcpKeys() })
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
    enabled: Boolean(keyId),
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

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <h2 className="text-lg font-medium">接入中心</h2>
            <p className="mt-1 text-sm text-mist">按 Key 生成接入配置与 AI 提示词，只包含该 Key 已授权的能力。</p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              title="刷新"
              onClick={() => {
                void integration.refetch()
                void reveal.refetch()
              }}
            >
              <RefreshCw size={15} />
            </Button>
            <Select value={keyId ? String(keyId) : ''} onChange={(event) => selectKey(event.target.value)}>
              {(keys.data || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.key_prefix} · {item.status}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {data ? (
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex flex-wrap gap-2">
              {capabilities.map((capability) => (
                <CapabilityChip key={capability.capability_id} capability={capability} />
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
              <Badge tone={data.key.status === 'active' ? 'ok' : 'warn'}>{data.key.status}</Badge>
              <span className="break-all">MCP {data.mcp_url}</span>
              <span className="break-all">REST {data.rest_base_url}</span>
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
        <div className="grid min-w-0 gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
          <div className="grid grid-cols-2 gap-2 lg:flex lg:flex-col">
            {METHODS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setMethod(item.id)}
                className={cn(
                  'w-full min-h-14 rounded-md border px-3 py-2 text-left transition lg:min-h-0',
                  method === item.id
                    ? 'border-signal/50 bg-signal/10 text-signal'
                    : 'border-line text-mist hover:border-mist/40 hover:text-paper',
                )}
              >
                <div className="text-sm font-medium">{item.label}</div>
                <div className="mt-0.5 text-[11px] text-mist">{item.desc}</div>
              </button>
            ))}
          </div>

          <Card className="min-w-0 space-y-3 overflow-hidden p-4">
            {method === 'mcp' ? (
              <>
                <h3 className="font-medium">MCP 接入</h3>
                <p className="text-xs text-mist">Streamable HTTP，鉴权 Header 用上面的密钥。多个客户端的配置可由 AI 按通用 JSON 适配。</p>
                <ToolList capabilities={authorized} />
                <div className="flex flex-wrap gap-2">
                  <Button type="button" disabled={!input} onClick={() => setDialog('mcp')}>
                    复制为 AI 提示词
                  </Button>
                  <Button
                    type="button"
                    variant="line"
                    disabled={!input || !authorized.length}
                    onClick={() => input && void copyTextOrNotify(buildMcpClientConfig(input, []), 'MCP 配置')}
                  >
                    复制 MCP 配置
                  </Button>
                </div>
              </>
            ) : null}

            {method === 'rest' ? (
              <>
                <h3 className="font-medium">Skill + REST 接入</h3>
                <p className="text-xs text-mist">不使用 MCP 时，把提示词交给 AI，由它用 HTTP 调用下面的接口。</p>
                <div className="space-y-3">
                  {authorized.map((capability) => (
                    <div key={capability.capability_id} className="rounded-md border border-line bg-ink/50 p-3">
                      <div className="text-sm text-paper">
                        {capability.capability_id} · {capability.name}
                      </div>
                      <div className="mt-1.5 space-y-1">
                        {(capability.integration?.rest_endpoints || []).map((endpoint) => (
                          <div key={`${endpoint.method}${endpoint.path}`} className="font-mono text-xs text-mist">
                            {endpoint.method} {endpoint.path}
                            {endpoint.summary ? <span className="ml-2 font-sans">{endpoint.summary}</span> : null}
                          </div>
                        ))}
                        {!(capability.integration?.rest_endpoints || []).length ? (
                          <div className="font-mono text-xs text-mist">
                            POST /v1/capabilities/{capability.capability_id}/&#123;operation&#125;
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                  {!authorized.length ? <div className="text-sm text-mist">该 Key 没有可用能力。</div> : null}
                </div>
                <Button type="button" disabled={!input || !authorized.length} onClick={() => setDialog('rest')}>
                  复制为 AI 提示词
                </Button>
              </>
            ) : null}

            {method === 'client' ? (
              <>
                <h3 className="font-medium">客户端配置</h3>
                <p className="text-xs text-mist">通用 MCP 客户端 JSON，复制后让 AI 按你的客户端调整格式。</p>
                <pre className="max-w-full min-w-0 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
                  {input ? buildMcpClientConfig(input, []) : '加载中…'}
                </pre>
                <Button
                  type="button"
                  disabled={!input}
                  onClick={() => input && void copyTextOrNotify(buildMcpClientConfig(input, []), '配置')}
                >
                  <Copy size={14} /> 复制配置
                </Button>
              </>
            ) : null}

            {method === 'reference' ? (
              <>
                <h3 className="font-medium">快速参考</h3>
                {authorized.map((capability) => (
                  <div key={capability.capability_id} className="rounded-md border border-line bg-ink/50 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm text-paper">
                          {capability.capability_id} · {capability.name}
                        </div>
                        <div className="mt-0.5 break-words text-xs text-mist [overflow-wrap:anywhere]">{capability.description}</div>
                      </div>
                      <Button
                        type="button"
                        variant="line"
                        className="shrink-0 px-2 py-1 text-xs"
                        disabled={!input}
                        onClick={() =>
                          input &&
                          void copyTextOrNotify(buildCapabilityReference(input, capability.capability_id), '参考')
                        }
                      >
                        复制
                      </Button>
                    </div>
                  </div>
                ))}
                {!authorized.length ? <div className="text-sm text-mist">该 Key 没有可用能力。</div> : null}
              </>
            ) : null}
          </Card>
        </div>
      ) : null}

      {dialog && input ? (
        <ItemPickDialog
          title={dialog === 'mcp' ? '复制 AI 提示词（MCP）' : '复制 AI 提示词（Skill + REST）'}
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
        <div className="text-[11px] text-mist">最近更新 {formatTime(keys.data[0].created_at)}</div>
      ) : null}
    </div>
  )
}

export default McpDocsPage
