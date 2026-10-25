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

export type ToolParamRow = {
  name: string
  type: string
  required: boolean
  description: string
}

function paramDescription(value: ToolSchema | undefined): string {
  if (value && typeof value.description === 'string' && value.description) return value.description
  if (value && Array.isArray(value.enum) && value.enum.length) return `可选值：${value.enum.join(' / ')}`
  return '—'
}

/** 结构化工具参数：界面表格与 markdown 生成共用同一份数据。 */
export function toolParams(tool: IntegrationTool): ToolParamRow[] {
  const schema = tool.input_schema as ToolSchema
  const properties = schema?.properties || {}
  const required = new Set(schema?.required || [])
  return Object.entries(properties).map(([name, value]) => ({
    name,
    type: schemaType(value),
    required: required.has(name),
    description: paramDescription(value),
  }))
}

function markdownTable(header: string[], rows: string[][]): string[] {
  return [
    `| ${header.join(' | ')} |`,
    `| ${header.map(() => '---').join(' | ')} |`,
    ...rows.map((row) => `| ${row.join(' | ')} |`),
  ]
}

/** 单个工具的请求参数与返回，输出 markdown（参数用表格）。 */
function toolBlock(tool: IntegrationTool, capabilityId?: string): string[] {
  const title = capabilityId ? `[${capabilityId}] ${tool.name}` : tool.name
  const lines = [`#### ${title}`, tool.description || '（无描述）', '', '请求参数：']
  const params = toolParams(tool)
  if (params.length) {
    lines.push(
      ...markdownTable(
        ['参数', '类型', '必填', '说明'],
        params.map((param) => [param.name, param.type, param.required ? '是' : '否', param.description]),
      ),
    )
  } else {
    lines.push('无。')
  }
  if (tool.returns) lines.push('', `返回：${tool.returns}`)
  return lines
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
      tools.push(...toolBlock(tool, capability.capability_id))
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

/**
 * 单个能力的自包含 Skill 文档：接入信息（含密钥）、接口、参数表、返回与使用说明。
 * 复制出去即可直接交给 AI 使用，无需再补充上下文。
 */
export function buildCapabilitySkill(input: IntegrationInput, capabilityId: string): string {
  const capability = input.capabilities.find((item) => item.capability_id === capabilityId)
  if (!capability) return ''
  const endpoints = capability.integration?.rest_endpoints || []
  const notes = capability.integration?.notes || []
  const lines: string[] = [
    `# ${capability.name}（${capability.capability_id}）`,
    capability.description || '（无描述）',
    '',
    '## 接入信息',
    `- Base URL：${input.restBaseUrl}`,
    `- 鉴权：Authorization: Bearer ${input.keyValue}`,
    '- 请求体：application/json',
    '- 错误体：{"error":{"type","message"}}，失败时读取 message',
    '',
  ]
  if (endpoints.length) {
    lines.push('## REST 接口', '')
    lines.push(
      ...markdownTable(
        ['方法', '路径', '说明'],
        endpoints.map((endpoint) => [
          endpoint.method,
          `${input.restBaseUrl}${endpoint.path}`,
          endpoint.summary || '—',
        ]),
      ),
      '',
    )
  }
  if (capability.tools.length) {
    lines.push('## 工具与参数', '')
    for (const tool of capability.tools) lines.push(...toolBlock(tool), '')
  }
  if (notes.length) {
    lines.push('## 约束', ...notes.map((note) => `- ${note}`), '')
  }
  lines.push('## 使用说明')
  lines.push('1. 把上面的 Base URL 与 Bearer 密钥配置到 HTTP 工具或代码中。')
  lines.push('2. 按「REST 接口」选择路径，请求体为 JSON，字段见「工具与参数」的请求参数表。')
  lines.push('3. 解析响应结果；失败时读取错误体的 message 并重试。')
  const example = endpoints[0]
  if (example) {
    lines.push(
      '',
      '示例：',
      '```bash',
      `curl -sS -X ${example.method} "${input.restBaseUrl}${example.path}" \\`,
      `  -H "Authorization: Bearer ${input.keyValue}" \\`,
      '  -H "Content-Type: application/json" \\',
      "  -d '<按请求参数表填写 JSON>'",
      '```',
    )
  }
  return lines.join('\n').trim()
}
