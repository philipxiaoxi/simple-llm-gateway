export type IntegrationRestEndpoint = {
  method: string
  path: string
  summary?: string
  content_type?: string
}

export type IntegrationTool = {
  name: string
  description: string
  operation?: string
  input_schema: Record<string, unknown>
  returns?: string
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
  tools: IntegrationTool[]
}

export type IntegrationInput = {
  keyName: string
  keyValue: string
  mcpUrl: string
  restBaseUrl: string
  capabilities: IntegrationCapability[]
}

type ToolSchema = {
  type?: string
  description?: string
  enum?: unknown[]
  properties?: Record<string, ToolSchema>
  items?: ToolSchema
  required?: string[]
}

function selectedCapabilities(input: IntegrationInput, selectedIds: string[]): IntegrationCapability[] {
  const chosen = new Set(selectedIds)
  return input.capabilities.filter((item) => item.authorized && chosen.has(item.capability_id))
}

function schemaType(schema: ToolSchema | undefined): string {
  if (!schema) return 'any'
  if (Array.isArray(schema.enum) && schema.enum.length) return schema.enum.map((value) => String(value)).join(' | ')
  const type = schema.type || 'any'
  if (type === 'array') return `${schemaType(schema.items)}[]`
  return type
}

/** 逐行渲染参数：名称、类型、必填/可选、说明（含枚举值）。 */
function paramLines(schema: ToolSchema | undefined): string[] {
  const properties = schema?.properties || {}
  const required = new Set(schema?.required || [])
  return Object.entries(properties).map(([key, value]) => {
    const head = `${key}: ${schemaType(value)}，${required.has(key) ? '必填' : '可选'}`
    const detail =
      typeof value?.description === 'string' && value.description
        ? value.description
        : Array.isArray(value?.enum)
          ? `可选值：${value.enum.join(' / ')}`
          : ''
    return `    - ${head}${detail ? `：${detail}` : ''}`
  })
}

/** 单个工具的参数与返回说明块。 */
function toolBlock(tool: IntegrationTool): string[] {
  const lines = [`- ${tool.name}：${tool.description}`]
  const params = paramLines(tool.input_schema as ToolSchema)
  if (params.length) lines.push('  参数：', ...params)
  if (tool.returns) lines.push(`  返回：${tool.returns}`)
  return lines
}

/** 供界面展示：工具的参数与返回说明（多行）。 */
export function describeTool(tool: IntegrationTool): string {
  return toolBlock(tool).join('\n')
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
      const block = toolBlock(tool)
      block[0] = `- [${capability.capability_id}] ${tool.name}：${tool.description}`
      tools.push(...block)
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
    '可用工具（仅这些，不要编造；参数带类型与必填标记，返回字段用于解析结果）：',
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
    if (capability.tools.length) {
      lines.push('请求参数与返回（请求体为 JSON，字段即下列参数）：')
      for (const tool of capability.tools) lines.push(...toolBlock(tool))
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
    '- 请求体：application/json',
    '- 错误体：{"error":{"type","message"}}，失败时读取 message',
    '',
    '可用接口与参数（仅这些）：',
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
    for (const tool of tools) lines.push(...toolBlock(tool))
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
