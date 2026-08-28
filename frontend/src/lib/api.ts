const TOKEN_KEY = 'gateway_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  // 管理接口禁止复用浏览器缓存。/api/admin/skills 曾被缓存成 index.html，导致列表一直为空。
  const response = await fetch(path, { ...init, cache: 'no-store', headers })
  // 401 强制登出只对管理后台接口生效，/api/share 等自助页面无需登录
  if (response.status === 401 && path.startsWith('/api/admin') && !path.endsWith('/login')) {
    clearToken()
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || body.message || message
    } catch {
      /* ignore */
    }
    throw new ApiError(response.status, typeof message === 'string' ? message : JSON.stringify(message))
  }
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    throw new ApiError(response.status, '接口返回了非 JSON 响应，请刷新页面后重试')
  }
  return response.json() as Promise<T>
}

export type Provider = {
  id: string
  label: string
  auth_type: string
  base_url: string
  models: string[]
}

export type QuotaItem = {
  label: string
  type: 'text' | 'progress'
  value: string | number
}

export type AccountQuota = {
  ok?: boolean
  message?: string
  items?: QuotaItem[]
}

export type Account = {
  id: number
  name: string
  provider: string
  source: 'upstream' | 'agent'
  agent_route_id: string | null
  auth_type: string
  base_url: string
  website_url: string | null
  status: string
  risk_level: string
  has_credential: boolean
  api_key?: string | null
  last_probe_ok: boolean | null
  last_probe_latency_ms: number | null
  last_probe_message: string | null
  last_probe_at: string | null
  quota: AccountQuota | null
  quota_updated_at: string | null
  models: string[]
  model_prefix?: string | null
  models_updated_at: string | null
  oauth_expires_at: string | null
  created_at: string
}

export type SkillClassificationSettings = {
  account_id: number | null
  account_name: string | null
  model: string | null
  enabled: boolean
  report_account_id: number | null
  report_account_name: string | null
  report_model: string | null
  report_enabled: boolean
}

export type DesktopTool = {
  id: number
  tool_id: string
  platform: string
  name: string
  description: string
  icon: string | null
  script_name: string
  status: 'not_downloaded' | 'downloading' | 'downloaded' | 'failed'
  file_name: string | null
  file_size: number | null
  version: string | null
  error_message: string | null
  updated_at: string
}

export type DesktopToolRun = {
  id: number
  tool_id: number
  status: 'running' | 'downloaded' | 'failed' | 'stopped'
  error_message: string | null
  started_at: string
  finished_at: string | null
}

export type DesktopToolRunDetail = DesktopToolRun & { lines: string[] }

export type CcSwitchTarget = {
  app: string
  label: string
  needs_dialog: boolean
  url?: string
}

export type ShareLookup = {
  name: string
  account_name: string
  account_source: 'upstream' | 'agent'
  provider: string
  provider_label: string
  risk_level: string
  status: string
  account_status: string
  today_tokens: number
  total_tokens: number
  models: string[]
  gateway: {
    origin: string
    anthropic_base_url: string
    openai_base_url: string
  }
  targets: CcSwitchTarget[]
  vscode: Record<string, unknown>
}

export type ApiKeySort = 'created_at' | 'tokens' | 'last_used'

export type KeyBoundAccount = {
  id: number
  name: string
  provider: string
  source: 'upstream' | 'agent'
  status: string
  model_prefix?: string | null
}

export type ApiKeyItem = {
  id: number
  name: string
  key_prefix: string
  key?: string | null
  account_id: number | null
  account_name: string
  provider: string
  account_source: 'upstream' | 'agent'
  risk_level: string
  status: string
  created_at: string
  last_used_at: string | null
  today_tokens: number
  total_tokens: number
  account_ids?: number[]
  accounts?: KeyBoundAccount[]
}

export type LogItem = {
  id: number
  account_id: number
  account_name?: string
  account_source: 'upstream' | 'agent'
  api_key_id: number | null
  api_key_name?: string
  protocol: string
  model: string | null
  stream: boolean
  status: string
  http_status: number
  error_message: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  latency_ms: number
  created_at: string
  updated_at?: string | null
  request_body?: unknown
  response_body?: unknown
}

export type Dashboard = {
  account_count: number
  unhealthy_count: number
  today_requests: number
  today_failures: number
  today_tokens: number
  total_requests: number
  total_tokens: number
  benchmark_count: number
  skill_count: number
}

export type LeaderboardComponent = {
  score: number | null
  coverage: number | null
  metric_count: number | null
}

export type LeaderboardLocalMatch = {
  kind: string
  account_id: number
  account_name: string
  provider: string
  agent_id: string | null
  agent_route_id: string | null
  matched_model: string
}

export type LeaderboardEntry = {
  rank: number | null
  previous_rank: number | null
  rank_change: number | null
  slug: string
  name: string
  provider: string
  provider_slug: string | null
  released_at: string | null
  context_window_tokens: number | null
  pricing_kind: string | null
  pricing_official_model_id: string | null
  input_price_per_million_usd: number | null
  output_price_per_million_usd: number | null
  input_price_per_million_cny: number | null
  output_price_per_million_cny: number | null
  price_quote: string | null
  pricing_source_name: string | null
  pricing_source_url: string | null
  score: number | null
  uncertainty: number | null
  coverage: number | null
  confidence: string | null
  possible_rank_from: number | null
  possible_rank_to: number | null
  metric_count: number | null
  summary: string | null
  components: Record<string, LeaderboardComponent>
  local_covered?: boolean
  local_matches?: LeaderboardLocalMatch[]
}

export type Leaderboard = {
  source_url: string
  source_page: string
  fetched_at: string | null
  stale: boolean
  ttl_seconds: number
  min_refresh_seconds: number
  source_updated_label: string | null
  error_message: string | null
  unofficial: boolean
  items: LeaderboardEntry[]
  total: number
}

export type SkillItem = {
  id: number
  slug: string
  name: string
  description: string
  category: string
  platforms: string[]
  license: string | null
  version: string | null
  author: string | null
  source_name: string | null
  file_count: number
  size_bytes: number
  created_at: string
  updated_at: string
}

export type SkillFile = {
  path: string
  size: number
  is_text: boolean
}

export type SkillDetail = SkillItem & {
  skill_md: string
  files: SkillFile[]
  analysis: SkillAnalysis | null
  analysis_generated_at: string | null
}

export type SkillAnalysis = {
  summary: string
  use_cases: string[]
  capabilities: string[]
  inputs_outputs: string[]
  trigger_and_workflow: string[]
  dependencies: string[]
  permissions_and_risks: string[]
  limitations: string[]
  setup_suggestions: string[]
  example_tasks: string[]
  recommendation: string
  fit_score: number | null
  generated_by: string
}

export type SkillCategory = {
  name: string
  count: number
}

export type SkillCategoryItem = {
  id: number
  name: string
  sort_order: number
  keywords: string[]
  is_protected: boolean
  count: number
  created_at: string
}

export type SkillList = {
  items: SkillItem[]
  total: number
  categories: SkillCategory[]
}

export type SkillUploadResult = {
  items: SkillItem[]
  created: number
  skipped: { name: string; reason: string }[]
}

export type GatewayAgent = {
  agent_id: string
  status: 'online' | 'offline'
  last_connected_at: string | null
  last_disconnected_at: string | null
  routes: {
    id: string
    name: string
    provider: string
    models: string[]
    models_updated_at: string | null
    account_id?: number | null
    model_prefix?: string | null
  }[]
}

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; username: string }>('/api/admin/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<{ username: string }>('/api/admin/me'),
  updateMe: (payload: { current_password: string; username?: string; password?: string }) =>
    request<{ token: string; username: string }>('/api/admin/me', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  dashboard: () => request<Dashboard>('/api/admin/dashboard'),
  agents: () => request<{ items: GatewayAgent[]; total: number }>('/api/admin/agents'),
  agent: (agentId: string) => request<GatewayAgent>(`/api/admin/agents/${encodeURIComponent(agentId)}`),
  refreshAgentRouteModels: (agentId: string, routeId: string) =>
    request<{ ok: boolean; models: string[]; message?: string; source?: string }>(
      `/api/admin/agents/${encodeURIComponent(agentId)}/routes/${encodeURIComponent(routeId)}/models`,
      { method: 'POST' },
    ),
  providers: () => request<Provider[]>('/api/admin/providers'),
  accounts: () => request<Account[]>('/api/admin/accounts'),
  keyAccounts: () => request<Account[]>('/api/admin/accounts?include_agent=true'),
  account: (id: number, reveal = false) =>
    request<Account>(`/api/admin/accounts/${id}${reveal ? '?reveal=1' : ''}`),
  createAccount: (payload: Record<string, unknown>) =>
    request<Account>('/api/admin/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  updateAccount: (id: number, payload: Record<string, unknown>) =>
    request<Account>(`/api/admin/accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAccount: (id: number) => request<{ ok: boolean }>(`/api/admin/accounts/${id}`, { method: 'DELETE' }),
  exportAccounts: (password: string) =>
    request<Record<string, unknown>>('/api/admin/accounts/export', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  importAccounts: (password: string, payload: Record<string, unknown>) =>
    request<{ created: number; skipped: number }>('/api/admin/accounts/import', {
      method: 'POST',
      body: JSON.stringify({ password, payload }),
    }),
  probe: (id: number) => request<{ ok: boolean; latency_ms: number; message: string }>(`/api/admin/accounts/${id}/probe`, { method: 'POST' }),
  quota: (id: number) => request<AccountQuota>(`/api/admin/accounts/${id}/quota`, { method: 'POST' }),
  models: (id: number) =>
    request<{ ok: boolean; models: string[]; message?: string; source?: string }>(`/api/admin/accounts/${id}/models`, {
      method: 'POST',
    }),
  oauthStart: (id: number) => request<{ authorize_url: string; needs_paste: boolean }>(`/api/admin/accounts/${id}/oauth/start`),
  completeOauth: (payload: { account_id?: number; callback_url?: string; code?: string; state?: string }) =>
    request<{ ok: boolean }>('/api/admin/oauth/grok/callback', { method: 'POST', body: JSON.stringify(payload) }),
  keys: (sort: ApiKeySort = 'last_used') => request<ApiKeyItem[]>(`/api/admin/keys?sort=${sort}`),
  createKey: (payload: { name: string; account_ids: number[] }) =>
    request<ApiKeyItem>('/api/admin/keys', { method: 'POST', body: JSON.stringify(payload) }),
  key: (id: number) => request<ApiKeyItem>(`/api/admin/keys/${id}`),
  benchmark: (payload: { account_id: number; model: string; prompt: string; max_tokens: number }) =>
    request<BenchmarkResult>('/api/admin/benchmark', { method: 'POST', body: JSON.stringify(payload) }),
  saveBenchmarkRun: (payload: { prompt: string; max_tokens: number; results: BenchmarkResult[] }) =>
    request<BenchmarkRun>('/api/admin/benchmark/history', { method: 'POST', body: JSON.stringify(payload) }),
  benchmarkHistory: (page = 1, pageSize = 20) =>
    request<BenchmarkHistory>(`/api/admin/benchmark/history?page=${page}&page_size=${pageSize}`),
  benchmarkRun: (id: number) => request<BenchmarkRun>(`/api/admin/benchmark/history/${id}`),
  exportBenchmarkHistory: async () => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch('/api/admin/benchmark/history/export', { headers })
    if (!response.ok) throw new ApiError(response.status, '导出测速结果失败')
    return response.blob()
  },
  ccSwitch: (id: number) =>
    request<{
      display_name: string
      models: string[]
      targets: CcSwitchTarget[]
      vscode: Record<string, unknown>
    }>(`/api/admin/keys/${id}/cc-switch`),
  ccSwitchBuild: (
    id: number,
    payload: { app: string; model?: string; haiku_model?: string; sonnet_model?: string; opus_model?: string },
  ) => request<{ url: string }>(`/api/admin/keys/${id}/cc-switch`, { method: 'POST', body: JSON.stringify(payload) }),
  updateKey: (id: number, payload: Record<string, unknown>) =>
    request<ApiKeyItem>(`/api/admin/keys/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteKey: (id: number) => request<{ ok: boolean }>(`/api/admin/keys/${id}`, { method: 'DELETE' }),
  logs: (query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<{ items: LogItem[]; total: number; page: number; page_size: number }>(`/api/admin/logs${suffix}`)
  },
  log: (id: number, includeBodies = false) =>
    request<LogItem>(`/api/admin/logs/${id}?include_bodies=${includeBodies ? 'true' : 'false'}`),
  logMessages: (id: number, page: number, pageSize = 20) =>
    request<{ items: { role: string; content: unknown }[]; total: number; page: number; page_size: number }>(
      `/api/admin/logs/${id}/messages?page=${page}&page_size=${pageSize}`,
    ),
  shareLookup: (apiKey: string) =>
    request<ShareLookup>('/api/share/lookup', { method: 'POST', body: JSON.stringify({ api_key: apiKey }) }),
  shareCcSwitch: (payload: {
    api_key: string
    app: string
    model?: string
    haiku_model?: string
    sonnet_model?: string
    opus_model?: string
  }) => request<{ url: string }>('/api/share/cc-switch', { method: 'POST', body: JSON.stringify(payload) }),
  skills: (query: { q?: string; category?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.q) params.set('q', query.q)
    if (query.category) params.set('category', query.category)
    const suffix = params.toString() ? `?${params}` : ''
    // 走 /list，避开浏览器把 GET /api/admin/skills 缓存成 HTML 的问题。
    return request<SkillList>(`/api/admin/skills/list${suffix}`)
  },
  desktopTools: () => request<DesktopTool[]>('/api/admin/tools'),
  createDesktopTool: (payload: { tool_id: string; platform: string; name: string; description?: string; icon?: string; script: string }) =>
    request<DesktopTool>('/api/admin/tools', { method: 'POST', body: JSON.stringify(payload) }),
  updateDesktopTool: (id: number, payload: { name?: string; description?: string; icon?: string; platform?: string }) =>
    request<DesktopTool>(`/api/admin/tools/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteDesktopTool: (id: number) => request<void>(`/api/admin/tools/${id}`, { method: 'DELETE' }),
  desktopToolScript: (id: number) => request<{ script: string }>(`/api/admin/tools/${id}/script`),
  saveDesktopToolScript: (id: number, content: string) => {
    const body = new FormData()
    body.set('content', content)
    return request<{ ok: boolean; script: string }>(`/api/admin/tools/${id}/script`, { method: 'POST', body })
  },
  preDownloadTool: (id: number) => request<DesktopTool>(`/api/admin/tools/${id}/pre-download`, { method: 'POST' }),
  stopDownloadTool: (id: number) => request<DesktopTool>(`/api/admin/tools/${id}/stop`, { method: 'POST' }),
  desktopToolRuns: (id: number) => request<DesktopToolRun[]>(`/api/admin/tools/${id}/runs`),
  desktopToolRun: (toolId: number, runId: number) => request<DesktopToolRunDetail>(`/api/admin/tools/${toolId}/runs/${runId}`),
  downloadDesktopTool: (id: number) =>
    request<{ url: string }>(`/api/admin/tools/${id}/download-url`, { method: 'POST' }),
  skill: (id: number) => request<SkillDetail>(`/api/admin/skills/${id}`),
  analyzeSkill: (id: number) => request<SkillAnalysis>(`/api/admin/skills/${id}/analysis`, { method: 'POST' }),
  updateSkill: (id: number, payload: { name?: string; description?: string; category?: string }) =>
    request<SkillItem>(`/api/admin/skills/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteSkill: (id: number) => request<{ ok: boolean }>(`/api/admin/skills/${id}`, { method: 'DELETE' }),
  skillCategories: () => request<{ items: SkillCategoryItem[] }>('/api/admin/skills/categories'),
  createSkillCategory: (payload: { name: string; keywords?: string[] }) =>
    request<SkillCategoryItem>('/api/admin/skills/categories', { method: 'POST', body: JSON.stringify(payload) }),
  updateSkillCategory: (id: number, payload: { name?: string; keywords?: string[]; sort_order?: number }) =>
    request<SkillCategoryItem>(`/api/admin/skills/categories/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteSkillCategory: (id: number) => request<{ ok: boolean }>(`/api/admin/skills/categories/${id}`, { method: 'DELETE' }),
  skillClassificationSettings: () => request<SkillClassificationSettings>('/api/admin/skills/classification-settings'),
  updateSkillClassificationSettings: (payload: {
    account_id: number | null
    model: string | null
    enabled: boolean
    report_account_id: number | null
    report_model: string | null
    report_enabled: boolean
  }) => request<SkillClassificationSettings>('/api/admin/skills/classification-settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  }),
  uploadSkills: async (files: File[], category = '自动识别') => {
    const body = new FormData()
    body.append('category', category)
    files.forEach((file) => {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath
      body.append('files', file, relative || file.name)
    })
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch('/api/admin/skills/upload', { method: 'POST', headers, body })
    if (response.status === 401) {
      clearToken()
      if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
    }
    if (!response.ok) {
      let message = `请求失败 (${response.status})`
      try {
        const payload = await response.json()
        message = payload.detail || payload.message || message
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, typeof message === 'string' ? message : JSON.stringify(message))
    }
    return response.json() as Promise<SkillUploadResult>
  },
  replaceSkill: async (id: number, files: File[], category = '自动识别') => {
    const body = new FormData()
    body.append('category', category)
    files.forEach((file) => {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath
      body.append('files', file, relative || file.name)
    })
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(`/api/admin/skills/${id}/replace`, { method: 'POST', headers, body })
    if (!response.ok) {
      let message = `请求失败 (${response.status})`
      try {
        const payload = await response.json()
        message = payload.detail || payload.message || message
      } catch { /* ignore */ }
      throw new ApiError(response.status, typeof message === 'string' ? message : JSON.stringify(message))
    }
    return response.json() as Promise<SkillItem>
  },
  bulkUpdateSkills: async (files: File[], category = '自动识别') => {
    const body = new FormData()
    body.append('category', category)
    files.forEach((file) => {
      const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath
      body.append('files', file, relative || file.name)
    })
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch('/api/admin/skills/bulk-update', { method: 'POST', headers, body })
    if (!response.ok) {
      let message = `请求失败 (${response.status})`
      try {
        const payload = await response.json()
        message = payload.detail || payload.message || message
      } catch { /* ignore */ }
      throw new ApiError(response.status, typeof message === 'string' ? message : JSON.stringify(message))
    }
    return response.json() as Promise<SkillUploadResult>
  },
  downloadSkill: async (id: number) => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(`/api/admin/skills/${id}/download`, { headers })
    if (!response.ok) throw new ApiError(response.status, '下载 Skill 失败')
    return response.blob()
  },
  downloadSkillFile: async (id: number, path: string) => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(`/api/admin/skills/${id}/files/${path.split('/').map(encodeURIComponent).join('/')}`, {
      headers,
    })
    if (!response.ok) throw new ApiError(response.status, '下载文件失败')
    return response.blob()
  },
  leaderboard: (refresh = false) =>
    request<Leaderboard>(`/api/admin/leaderboard${refresh ? '?refresh=true' : ''}`),
  publicLeaderboard: () => request<Leaderboard>('/api/share/leaderboard'),
}

export type BenchmarkResult = {
  ok: boolean
  account_id: number
  account_name: string
  provider: string
  model: string
  timeout?: boolean
  first_token_ms?: number
  total_ms?: number
  output_chars?: number
  estimated_output_tokens?: number
  output_tokens_per_second?: number
  preview?: string
  error?: string
}

export type BenchmarkRun = {
  id: number
  prompt: string
  max_tokens: number
  created_at: string
  result_count: number
  success_count: number
  results?: BenchmarkResult[]
}

export type BenchmarkHistory = {
  items: BenchmarkRun[]
  total: number
  page: number
  page_size: number
}
