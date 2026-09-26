export type IntegrationRestEndpoint = {
  method: string
  path: string
  summary?: string
  content_type?: string
}

export type IntegrationCapability = {
  capability_id: string
  name: string
  description: string
  authorized: boolean
  integration?: {
    rest_endpoints?: IntegrationRestEndpoint[]
    notes?: string[]
  }
  tools: { name: string; description: string; input_schema: Record<string, unknown> }[]
}

export type IntegrationInput = {
  keyName: string
  keyValue: string
  mcpUrl: string
  restBaseUrl: string
  capabilities: IntegrationCapability[]
}

function selectedCapabilities(input: IntegrationInput, selectedIds: string[]): IntegrationCapability[] {
  const chosen = new Set(selectedIds)
  return input.capabilities.filter((item) => item.authorized && chosen.has(item.capability_id))
}

function toolSignature(tool: { name: string; input_schema: Record<string, unknown> }): string {
  const schema = tool.input_schema || {}
  const properties = (schema.properties as Record<string, unknown>) || {}
  const required = (schema.required as string[]) || []
  const args = Object.keys(properties).map((key) => (required.includes(key) ? `${key}` : `${key}?`))
  return `${tool.name}(${args.join(', ')})`
}

function notesLines(selected: IntegrationCapability[]): string[] {
  const notes: string[] = []
  for (const capability of selected) {
    for (const note of capability.integration?.notes || []) {
      notes.push(`[${capability.capability_id}] ${note}`)
    }
  }
  return notes
}

export function buildMcpPrompt(input: IntegrationInput, selectedIds: string[]): string {
  const selected = selectedCapabilities(input, selectedIds)
  const tools: string[] = []
  for (const capability of selected) {
    for (const tool of capability.tools) {
      tools.push(`- ${toolSignature(tool)}：${tool.description}`)
    }
  }
  const notes = notesLines(selected)
  return [
    `请帮我把下面这个 MCP 服务接入我正在使用的 AI 客户端。`,
    '',
    '接入信息：',
    `- 名称：${input.keyName}`,
    `- URL：${input.mcpUrl}`,
    '- 传输：Streamable HTTP',
    `- 鉴权：Header "Authorization: Bearer ${input.keyValue}"（也可用 x-api-key）`,
    '',
    '可用工具（仅这些，不要编造）：',
    ...(tools.length ? tools : ['- （未选择任何工具）']),
    '',
    '约束：',
    ...(notes.length ? notes.map((line) => `- ${line}`) : ['- 无']),
    '',
    '请根据我使用的客户端（如 Claude Desktop / Cursor / Cline / OpenCode）给出具体配置方法；密钥只用于该客户端配置，不要外传。',
  ].join('\n')
}

export function buildRestPrompt(input: IntegrationInput, selectedIds: string[]): string {
  const selected = selectedCapabilities(input, selectedIds)
  const blocks: string[] = []
  for (const capability of selected) {
    const endpoints = capability.integration?.rest_endpoints || []
    const lines = [`### ${capability.capability_id}（${capability.name}）`]
    if (endpoints.length) {
      for (const endpoint of endpoints) {
        const suffix = endpoint.content_type ? `  [${endpoint.content_type}]` : ''
        lines.push(`- ${endpoint.method} ${endpoint.path}  ${endpoint.summary || ''}${suffix}`.trimEnd())
      }
    } else {
      lines.push(`- POST /v1/capabilities/${capability.capability_id}/{operation}`)
    }
    blocks.push(lines.join('\n'))
  }
  const notes = notesLines(selected)
  return [
    `你是一个可以使用 HTTP 工具的 AI。请通过 REST 调用「${input.keyName}」能力完成任务。`,
    '',
    '环境：',
    `- Base URL：${input.restBaseUrl}`,
    `- 鉴权：Authorization: Bearer ${input.keyValue}`,
    '- 错误体：{"error":{"type","message"}}，失败时读取 message',
    '',
    '可用接口（仅这些）：',
    ...(blocks.length ? blocks : ['（未选择任何接口）']),
    '',
    '约束：',
    ...(notes.length ? notes.map((line) => `- ${line}`) : ['- 无']),
    '',
    '调用前先复述计划；分步执行并反馈结果。',
  ].join('\n')
}

export function buildMcpClientConfig(input: IntegrationInput, selectedIds: string[]): string {
  void selectedIds
  return JSON.stringify(
    {
      mcpServers: {
        [input.keyName]: {
          url: input.mcpUrl,
          headers: { Authorization: `Bearer ${input.keyValue}` },
        },
      },
    },
    null,
    2,
  )
}

export function buildCapabilityReference(input: IntegrationInput, capabilityId: string): string {
  const capability = input.capabilities.find((item) => item.capability_id === capabilityId)
  if (!capability) return ''
  const lines: string[] = [`# ${capability.name}（${capability.capability_id}）`, capability.description, '']
  const tools = capability.tools || []
  if (tools.length) {
    lines.push('MCP tools：')
    for (const tool of tools) lines.push(`- ${toolSignature(tool)}：${tool.description}`)
    lines.push('')
  }
  const endpoints = capability.integration?.rest_endpoints || []
  if (endpoints.length) {
    lines.push('REST：')
    for (const endpoint of endpoints) {
      lines.push(`- ${endpoint.method} ${input.restBaseUrl}${endpoint.path}  ${endpoint.summary || ''}`.trimEnd())
    }
    lines.push('')
  }
  const notes = capability.integration?.notes || []
  if (notes.length) {
    lines.push('约束：')
    for (const note of notes) lines.push(`- ${note}`)
  }
  return lines.join('\n').trim()
}
