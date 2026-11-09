import { ApiError, getToken } from './api'

export type AssistantStreamPage = {
  path: string
  title?: string
  selection?: string
}

type StreamOptions = {
  signal?: AbortSignal
  onEvent: (event: string, data: Record<string, unknown>) => void
}

function parseErrorBody(body: unknown, fallback: string) {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (detail && typeof detail === 'object') {
      const error = (detail as { error?: unknown }).error
      if (error && typeof error === 'object' && typeof (error as { message?: unknown }).message === 'string') {
        return (error as { message: string }).message
      }
    }
    if (typeof detail === 'string') return detail
  }
  return fallback
}

async function readEventStream(
  response: Response,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  if (!response.body) throw new ApiError(500, '浏览器没有收到流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const flush = (block: string) => {
    let event = ''
    const dataLines: string[] = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    }
    if (!event || !dataLines.length) return
    const raw = dataLines.join('\n')
    let data: Record<string, unknown>
    try {
      const parsed = JSON.parse(raw)
      data = parsed && typeof parsed === 'object' ? parsed : { value: parsed }
    } catch {
      data = { raw }
    }
    onEvent(event, data)
  }
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) flush(block)
    }
    if (buffer.trim()) flush(buffer)
  } finally {
    reader.releaseLock()
  }
}

function assistantHeaders() {
  const headers = new Headers()
  headers.set('Content-Type', 'application/json')
  headers.set('X-GW-Client', 'web')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return headers
}

export async function streamAssistantMessage(
  conversationId: string,
  payload: { content: string; page?: AssistantStreamPage },
  { signal, onEvent }: StreamOptions,
) {
  const response = await fetch(`/api/admin/assistant/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: assistantHeaders(),
    body: JSON.stringify(payload),
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, parseErrorBody(body, `请求失败 (${response.status})`))
  }
  await readEventStream(response, onEvent)
}

export async function streamApprovalDecision(
  approvalId: string,
  payload: { decision: 'approve' | 'reject'; page?: AssistantStreamPage },
  { signal, onEvent }: StreamOptions,
) {
  const response = await fetch(`/api/admin/assistant/approvals/${approvalId}/decision`, {
    method: 'POST',
    headers: assistantHeaders(),
    body: JSON.stringify(payload),
    signal,
    cache: 'no-store',
  })
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, parseErrorBody(body, `请求失败 (${response.status})`))
  }
  await readEventStream(response, onEvent)
}

const TOOL_LABELS: Record<string, string> = {
  api_search: '搜索站点接口',
  api_describe: '读取接口参数',
  api_call: '调用站点接口',
  mcp_search: '搜索 MCP 能力',
  mcp_describe: '读取 MCP 参数',
  mcp_call: '调用 MCP 能力',
  ui_navigate: '打开页面',
  douyin_parse: '解析抖音视频',
  douyin_job: '查询抖音任务',
  douyin_result: '读取抖音结果',
  douyin_list: '列出抖音任务',
  image_generate: '生成图片',
  video_generate: '生成视频',
  generation_job: '查询生成任务',
  generation_result: '读取生成结果',
  generation_list: '列出生成任务',
  knowledge_list: '列出知识库',
  knowledge_search: '检索知识库',
  docparse_convert: '转换文档',
  docparse_job: '查询转换任务',
  docparse_result: '读取转换结果',
  site_deploy: '部署站点',
  site_list: '列出站点',
  site_status: '查看站点状态',
  site_update: '更新站点',
  site_rollback: '回滚站点',
  site_delete: '删除站点',
  site_access: '设置站点访问',
  site_access_info: '读取站点地址',
  diagram_schema: '读取图表规范',
  diagram_create: '创建图表',
  diagram_update: '更新图表',
  diagram_list: '列出图表',
  diagram_status: '查看图表状态',
  diagram_get_source: '读取图表源码',
  diagram_rollback: '回滚图表',
  diagram_delete: '删除图表',
  diagram_access: '设置图表访问',
  diagram_access_info: '读取图表地址',
  info_report: '上报资讯',
  info_report_batch: '批量上报资讯',
  info_report_config: '查看上报配置',
}

export function toolLabel(name: string, fallback?: string) {
  return TOOL_LABELS[name] || fallback || name
}

export function toolLink(name: string, result: unknown): string | null {
  if (!result || typeof result !== 'object') return null
  const data = result as Record<string, unknown>
  const id = String(data.id ?? data.job_id ?? data.site_id ?? data.siteId ?? '')
  if (name.startsWith('douyin') && id) return `/mcp-plaza/douyin/${id}`
  if (name.startsWith('generation')) {
    const kind = String(data.kind ?? '')
    if (kind === 'video') return '/video-gen'
    return '/image-gen'
  }
  if (name.startsWith('site') && id) return `/mcp-plaza/sites/${id}`
  if (name.startsWith('diagram') && id) return `/mcp-plaza/diagrams/${id}`
  if (name.startsWith('docparse')) return '/mcp-plaza/docparse'
  if (name.startsWith('knowledge')) return '/mcp-plaza/knowledge'
  if (name.startsWith('info')) return '/info'
  if (name.startsWith('portrait')) return '/mcp-plaza/portraits'
  if (name.startsWith('pose')) return '/mcp-plaza/poses'
  if (name.startsWith('costume')) return '/mcp-plaza/costumes'
  if (name.startsWith('handdraw')) return '/mcp-plaza/handdraws'
  return null
}

export function toolStatusLabel(status: string) {
  const map: Record<string, string> = {
    queued: '排队中',
    submitting: '提交中',
    running: '处理中',
    resolving: '解析中',
    downloading: '下载中',
    succeeded: '已完成',
    partial: '部分完成',
    failed: '失败',
    violation: '违规拦截',
    ready: '就绪',
    processing: '处理中',
  }
  return map[status] || status
}
