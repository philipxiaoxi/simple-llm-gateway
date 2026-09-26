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
      const detail = body.detail
      const nested =
        detail && typeof detail === 'object' && detail.error && typeof detail.error.message === 'string'
          ? detail.error.message
          : ''
      message = nested || (typeof detail === 'string' ? detail : '') || body.message || message
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

export type ModelCaps = {
  id: string
  context_window: number | null
  max_output_tokens: number | null
  reasoning: boolean
  reasoning_efforts: string[] | null
  modalities: { input: string[]; output: string[] }
  source: string
  overridden: string[]
  overrides: Record<string, unknown>
  enabled?: boolean
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
  models: ModelCaps[]
  model_prefix?: string | null
  header_spoof: 'none' | 'grok' | 'opencode'
  models_updated_at: string | null
  oauth_expires_at: string | null
  created_at: string
}

export type AccountUsageItem = {
  kind: string
  id: string | null
  name: string
  detail: string
  action: string
  severity: 'info' | 'warning'
}

export type AccountUsage = {
  account_id: number
  account_name: string
  has_relations: boolean
  items: AccountUsageItem[]
  risks: string[]
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

export type ShareModelEntry = {
  id: string
  raw_id: string
  account_id: number
  account_name: string
  account_source: 'upstream' | 'agent'
  provider: string
  account_index: number
}

export type ShareAlias = {
  alias: string
  model: string
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
  accounts: Array<{
    id: number
    name: string
    source: 'upstream' | 'agent'
    provider: string
    status: string
    risk_level: string
    model_prefix: string
  }>
  today_tokens: number
  total_tokens: number
  models: string[]
  aliases?: ShareAlias[]
  model_caps?: ModelCaps[]
  model_entries?: ShareModelEntry[]
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
  risk_level: string
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

export type DashboardLeaderboardTop = {
  rank: number | null
  name: string
  provider: string
  score: number | null
  slug: string
  context_window_tokens: number | null
  max_output_tokens: number | null
}

export type DashboardBenchmarkTop = {
  model: string
  account_name: string
  provider: string
  output_tokens_per_second: number
  first_token_ms: number | null
  total_ms: number | null
  run_id: number | null
  created_at: string | null
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
  key_count: number
  tool_count: number
  agent_count: number
  agent_online_count: number
  leaderboard_top: DashboardLeaderboardTop[]
  benchmark_speed_top: DashboardBenchmarkTop[]
}

export type LeaderboardComponent = {
  score: number | null
  coverage: number | null
  metric_count: number | null
}

export type JobParam = {
  key: string
  label: string
  value: number
  min: number
  max: number
  hint: string
}

export type ScheduledJob = {
  id: string
  name: string
  description: string
  kind: 'loop' | 'on_demand' | string
  source_url: string | null
  running: boolean
  last_started_at: string | null
  last_finished_at: string | null
  last_ok: boolean | null
  last_message: string | null
  error_message: string | null
  next_run_at: string | null
  cache_fetched_at: string | null
  cache_expires_at: string | null
  cache_ok: boolean | null
  ttl_seconds: number
  params: JobParam[]
  details: Record<string, unknown>
}

export type JobList = {
  items: ScheduledJob[]
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

export type LeaderboardBenchmark = {
  model: string
  account_name: string
  provider: string
  output_tokens_per_second: number
  first_token_ms: number | null
  total_ms: number | null
  created_at: string | null
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
  max_output_tokens: number | null
  pricing_kind: string | null
  pricing_official_model_id: string | null
  input_price_per_million_usd: number | null
  output_price_per_million_usd: number | null
  cache_input_price_per_million_cny: number | null
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
  benchmark?: LeaderboardBenchmark | null
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

export type SkillBundleItem = {
  id: number
  name: string
  description: string
  member_count: number
  created_at: string
  updated_at: string
}

export type SkillBundleMember = {
  skill_id: number
  added_at: string
  missing: boolean
  skill: SkillItem | null
}

export type SkillBundleDetail = SkillBundleItem & {
  members: SkillBundleMember[]
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
    models: ModelCaps[]
    models_updated_at: string | null
    account_id?: number | null
    model_prefix?: string | null
    status: string
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
    request<{ ok: boolean; models: ModelCaps[]; message?: string; source?: string }>(
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
  accountUsage: (id: number) => request<AccountUsage>(`/api/admin/accounts/${id}/usage`),
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
    request<{ ok: boolean; models: ModelCaps[]; message?: string; source?: string }>(`/api/admin/accounts/${id}/models`, {
      method: 'POST',
    }),
  addAccountModel: (id: number, modelId: string) =>
    request<{ created: boolean; model: ModelCaps }>(`/api/admin/accounts/${id}/models/custom`, {
      method: 'POST',
      body: JSON.stringify({ id: modelId }),
    }),
  removeAccountModel: (id: number, modelId: string) =>
    request<{ removed: boolean; id: string }>(`/api/admin/accounts/${id}/models/custom/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    }),
  updateAccountModel: (id: number, modelId: string, payload: Record<string, unknown>) =>
    request<ModelCaps>(`/api/admin/accounts/${id}/models/${encodeURIComponent(modelId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
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
      model_caps?: ModelCaps[]
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
  keyAliases: (id: number) =>
    request<{ aliases: ShareAlias[]; models: string[] }>(`/api/admin/keys/${id}/aliases`),
  keyAliasSave: (id: number, payload: { alias: string; model: string }) =>
    request<{ aliases: ShareAlias[]; models: string[] }>(`/api/admin/keys/${id}/aliases`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  keyAliasRename: (id: number, payload: { old_alias: string; new_alias: string }) =>
    request<{ aliases: ShareAlias[]; models: string[] }>(`/api/admin/keys/${id}/aliases/rename`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  keyAliasDelete: (id: number, alias: string) =>
    request<{ aliases: ShareAlias[]; models: string[] }>(
      `/api/admin/keys/${id}/aliases/${encodeURIComponent(alias)}`,
      { method: 'DELETE' },
    ),
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
  logMessages: (id: number, page: number, pageSize = 20, aroundSeq?: number) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
    if (aroundSeq != null && Number.isFinite(aroundSeq)) params.set('around_seq', String(aroundSeq))
    return request<{
      items: { seq: number; role: string; content: unknown; tool_calls?: unknown }[]
      total: number
      page: number
      page_size: number
    }>(`/api/admin/logs/${id}/messages?${params}`)
  },
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
  shareAliasSave: (payload: { api_key: string; alias: string; model: string }) =>
    request<{ aliases: ShareAlias[] }>('/api/share/aliases/save', { method: 'POST', body: JSON.stringify(payload) }),
  shareAliasRename: (payload: { api_key: string; old_alias: string; new_alias: string }) =>
    request<{ aliases: ShareAlias[] }>('/api/share/aliases/rename', { method: 'POST', body: JSON.stringify(payload) }),
  shareAliasDelete: (payload: { api_key: string; alias: string }) =>
    request<{ aliases: ShareAlias[] }>('/api/share/aliases/delete', { method: 'POST', body: JSON.stringify(payload) }),
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
  skillDownloadUrl: (id: number) =>
    request<{ url: string; expiresInSeconds: number }>(`/api/admin/skills/${id}/download-url`, { method: 'POST' }),
  skillUploadUrl: () =>
    request<{ url: string; expiresInSeconds: number }>('/api/admin/skills/upload-url', { method: 'POST' }),
  listSkillBundles: (q = '') => {
    const query = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
    return request<{ items: SkillBundleItem[]; total: number }>(`/api/admin/skill-bundles${query}`)
  },
  createSkillBundle: (payload: { name: string; description?: string }) =>
    request<SkillBundleItem>('/api/admin/skill-bundles', { method: 'POST', body: JSON.stringify(payload) }),
  getSkillBundle: (id: number) => request<SkillBundleDetail>(`/api/admin/skill-bundles/${id}`),
  updateSkillBundle: (id: number, payload: { name?: string; description?: string }) =>
    request<SkillBundleItem>(`/api/admin/skill-bundles/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteSkillBundle: (id: number) => request<{ ok: boolean }>(`/api/admin/skill-bundles/${id}`, { method: 'DELETE' }),
  addSkillBundleMembers: (id: number, skillIds: number[]) =>
    request<{
      added: number
      skipped: { skill_id: number; reason: string }[]
      members: SkillBundleMember[]
    }>(`/api/admin/skill-bundles/${id}/members`, {
      method: 'POST',
      body: JSON.stringify({ skill_ids: skillIds }),
    }),
  removeSkillBundleMember: (id: number, skillId: number) =>
    request<{ ok: boolean }>(`/api/admin/skill-bundles/${id}/members/${skillId}`, { method: 'DELETE' }),
  downloadSkillBundle: async (id: number) => {
    const headers = new Headers()
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(`/api/admin/skill-bundles/${id}/download`, { headers })
    if (!response.ok) {
      let message = '下载组合包失败'
      try {
        const payload = await response.json()
        message = payload.detail || payload.message || message
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, typeof message === 'string' ? message : JSON.stringify(message))
    }
    return response.blob()
  },
  skillBundleDownloadUrl: (id: number) =>
    request<{ url: string; expiresInSeconds: number }>(`/api/admin/skill-bundles/${id}/download-url`, {
      method: 'POST',
    }),
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
  leaderboard: () => request<Leaderboard>('/api/admin/leaderboard'),
  publicLeaderboard: () => request<Leaderboard>('/api/share/leaderboard'),
  jobs: () => request<JobList>('/api/admin/jobs'),
  runJob: (id: string) => request<JobList>(`/api/admin/jobs/${id}/run`, { method: 'POST' }),
  updateJob: (id: string, payload: Record<string, number>) =>
    request<JobList>(`/api/admin/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  contentAuditFindings: (query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<ContentAuditFindingList>(`/api/admin/content-audit/findings${suffix}`)
  },
  contentAuditSummary: () => request<ContentAuditSummary>('/api/admin/content-audit/summary'),
  startContentAudit: () =>
    request<{ started: boolean; message: string }>('/api/admin/content-audit/scan/start', { method: 'POST' }),
  stopContentAudit: () =>
    request<{ stopped: boolean; message: string }>('/api/admin/content-audit/scan/stop', { method: 'POST' }),
  pauseContentAudit: () =>
    request<{ paused: boolean; message: string }>('/api/admin/content-audit/scan/pause', { method: 'POST' }),
  resumeContentAudit: () =>
    request<{ resumed: boolean; message: string }>('/api/admin/content-audit/scan/resume', { method: 'POST' }),
  syncContentAuditLexicon: () =>
    request<ContentAuditLexiconSync>('/api/admin/content-audit/lexicon/sync', { method: 'POST' }),

  // ---- 语音输入（手机 → 电脑）----
  voiceRooms: () => request<{ items: VoiceRoomSummary[]; total: number }>('/api/admin/voice/rooms'),
  voiceRoom: (roomId: string) => request<VoiceRoom>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}`),
  createVoiceRoom: (payload: VoiceRoomInput) =>
    request<VoiceRoom>('/api/admin/voice/rooms', { method: 'POST', body: JSON.stringify(payload) }),
  updateVoiceRoom: (roomId: string, payload: Partial<VoiceRoomInput> & { clearPin?: boolean; status?: string }) =>
    request<VoiceRoom>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteVoiceRoom: (roomId: string) =>
    request<{ ok: boolean }>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}`, { method: 'DELETE' }),
  rotateVoiceCode: (roomId: string) =>
    request<VoiceRoom>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}/rotate-code`, { method: 'POST' }),
  issueVoiceToken: (roomId: string, role = 'desktop', days = 30) =>
    request<VoiceTokenResponse>(
      `/api/admin/voice/rooms/${encodeURIComponent(roomId)}/token?role=${role}&days=${days}`,
      { method: 'POST' },
    ),
  voiceLive: (roomId: string) =>
    request<VoiceLive>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}/live`),
  voiceSessions: (roomId: string, query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<VoiceSessionList>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}/sessions${suffix}`)
  },
  voiceSegments: (roomId: string, query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<VoiceSegmentList>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}/segments${suffix}`)
  },
  voiceEvents: (roomId: string, query: Record<string, string | number | undefined> = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<VoiceEventList>(`/api/admin/voice/rooms/${encodeURIComponent(roomId)}/events${suffix}`)
  },
  voicePolishAccounts: () => request<{ items: VoicePolishAccount[]; total: number }>('/api/admin/voice/polish-accounts'),
  testVoicePolish: (payload: { text: string; accountId?: number | null; model?: string | null; mode?: string; roomId?: string }) =>
    request<VoicePolishTestResult>('/api/admin/voice/test-polish', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ---- 手机端公开接口（不带管理员令牌）----
  voiceLookup: (code: string) =>
    request<{ roomId: string; name: string; requirePin: boolean }>(
      `/api/voice/rooms/lookup?code=${encodeURIComponent(code)}`,
    ),
  voiceJoin: (roomId: string, payload: { code?: string; pin?: string; clientUid: string; name: string }) =>
    request<VoiceJoinResponse>(`/api/voice/rooms/${encodeURIComponent(roomId)}/join`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ---- MCP 广场 ----
  mcpCatalog: () => request<{ items: McpCatalogItem[] }>('/api/admin/mcp/catalog'),
  docparseJobs: (query: { status?: string; q?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.status) params.set('status', query.status)
    if (query.q) params.set('q', query.q)
    const suffix = params.toString() ? `?${params}` : ''
    return request<DocParseJobList>(`/api/admin/mcp/docparse/jobs${suffix}`)
  },
  createDocParseJob: (file: File, onProgress?: (percent: number) => void) =>
    uploadDocParse(file, onProgress),
  docparseJob: (id: string) => request<DocParseJob>(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(id)}`),
  retryDocParseJob: (id: string) =>
    request<DocParseJob>(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  cancelDocParseJob: (id: string) =>
    request<DocParseJob>(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  deleteDocParseJob: (id: string) =>
    request<void>(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  ingestDocParseJob: (id: string, kbId: string) => {
    const form = new FormData()
    form.set('kb_id', kbId)
    return request<DocParseJob>(`/api/admin/mcp/docparse/jobs/${encodeURIComponent(id)}/ingest`, {
      method: 'POST',
      body: form,
    })
  },
  mcpSites: (query: { q?: string; status?: string } = {}) => {
    const params = new URLSearchParams()
    if (query.q) params.set('q', query.q)
    if (query.status) params.set('status', query.status)
    const suffix = params.toString() ? `?${params}` : ''
    return request<McpSiteList>(`/api/admin/mcp/sites${suffix}`)
  },
  mcpSite: (siteId: string) => request<McpSite>(`/api/admin/mcp/sites/${encodeURIComponent(siteId)}`),
  mcpSiteVersion: (siteId: string, versionId: string) =>
    request<McpSiteVersion>(
      `/api/admin/mcp/sites/${encodeURIComponent(siteId)}/versions/${encodeURIComponent(versionId)}`,
    ),
  createMcpSite: (
    file: File,
    fields: { slug?: string; name?: string; entry?: string; activate?: boolean } = {},
    onProgress?: (percent: number) => void,
  ) => uploadSite('/api/admin/mcp/sites', file, fields, onProgress),
  createMcpSiteVersion: (
    siteId: string,
    file: File,
    fields: { entry?: string; activate?: boolean } = {},
    onProgress?: (percent: number) => void,
  ) =>
    uploadSite(`/api/admin/mcp/sites/${encodeURIComponent(siteId)}/versions`, file, fields, onProgress),
  updateMcpSite: (
    siteId: string,
    payload: { name?: string; description?: string; access_mode?: string; entry_file?: string; spa_fallback?: boolean; status?: string },
  ) => request<McpSite>(`/api/admin/mcp/sites/${encodeURIComponent(siteId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteMcpSite: (siteId: string) =>
    request<void>(`/api/admin/mcp/sites/${encodeURIComponent(siteId)}`, { method: 'DELETE' }),
  activateMcpSiteVersion: (siteId: string, versionId: string) =>
    request<McpSite>(
      `/api/admin/mcp/sites/${encodeURIComponent(siteId)}/versions/${encodeURIComponent(versionId)}/activate`,
      { method: 'POST' },
    ),
  retryMcpSiteVersion: (siteId: string, versionId: string) =>
    request<McpSiteVersion>(
      `/api/admin/mcp/sites/${encodeURIComponent(siteId)}/versions/${encodeURIComponent(versionId)}/retry`,
      { method: 'POST' },
    ),
  deleteMcpSiteVersion: (siteId: string, versionId: string) =>
    request<void>(
      `/api/admin/mcp/sites/${encodeURIComponent(siteId)}/versions/${encodeURIComponent(versionId)}`,
      { method: 'DELETE' },
    ),
  generateMcpSiteToken: (siteId: string) =>
    request<{ token: string; site: McpSite }>(`/api/admin/mcp/sites/${encodeURIComponent(siteId)}/token`, {
      method: 'POST',
    }),
  mcpKeys: () => request<McpKeyItem[]>('/api/admin/mcp/keys'),
  revealMcpKey: (id: number) =>
    request<{ id: number; name: string; key: string }>(`/api/admin/mcp/keys/${id}/reveal`),
  mcpKeyIntegration: (id: number) =>
    request<McpKeyIntegration>(`/api/admin/mcp/keys/${id}/integration`),
  createMcpKey: (payload: { name: string; capability_ids: string[] }) =>
    request<McpKeyItem>('/api/admin/mcp/keys', { method: 'POST', body: JSON.stringify(payload) }),
  updateMcpKey: (id: number, payload: { name?: string; status?: string }) =>
    request<McpKeyItem>(`/api/admin/mcp/keys/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  updateMcpKeyCapabilities: (id: number, capability_ids: string[]) =>
    request<McpKeyItem>(`/api/admin/mcp/keys/${id}/capabilities`, {
      method: 'PUT',
      body: JSON.stringify({ capability_ids }),
    }),
  deleteMcpKey: (id: number) => request<void>(`/api/admin/mcp/keys/${id}`, { method: 'DELETE' }),
  mcpKnowledgeBases: () => request<McpKnowledgeBase[]>('/api/admin/mcp/knowledge/bases'),
  mcpKnowledgeEmbeddingAccounts: () =>
    request<McpKnowledgeEmbeddingAccount[]>('/api/admin/mcp/knowledge/embedding-accounts'),
  createMcpKnowledgeBase: (payload: {
    name: string
    description?: string
    scope?: string
    embedding_account_id?: number | null
    embedding_model?: string | null
    embedding_dimensions?: number | null
    allowed_mcp_key_ids?: number[]
  }) => request<McpKnowledgeBase>('/api/admin/mcp/knowledge/bases', { method: 'POST', body: JSON.stringify(payload) }),
  updateMcpKnowledgeBase: (
    kbId: string,
    payload: {
      name?: string
      description?: string
      scope?: string
      embedding_account_id?: number | null
      embedding_model?: string | null
      embedding_dimensions?: number | null
      allowed_mcp_key_ids?: number[]
    },
  ) =>
    request<McpKnowledgeBase>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  reindexMcpKnowledgeBase: (kbId: string, onlyStale: boolean) =>
    request<{ created: number }>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/reindex`, {
      method: 'POST',
      body: JSON.stringify({ only_stale: onlyStale }),
    }),
  deleteMcpKnowledgeBase: (kbId: string) =>
    request<void>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}`, { method: 'DELETE' }),
  mcpKnowledgeDocuments: (kbId: string) =>
    request<McpKnowledgeDocument[]>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/documents`),
  createMcpKnowledgeDocument: (kbId: string, payload: { text: string; source_name?: string }) =>
    request<McpKnowledgeDocument>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/documents`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteMcpKnowledgeDocument: (kbId: string, docId: string) =>
    request<void>(
      `/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`,
      { method: 'DELETE' },
    ),
  reembedMcpKnowledgeDocument: (kbId: string, docId: string) =>
    request<McpKnowledgeDocument>(
      `/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/reembed`,
      { method: 'POST' },
    ),

  // ---- 知识库采集任务 ----
  mcpKnowledgeJobs: (query: {
    status?: string
    kb_id?: string
    kind?: string
    q?: string
    limit?: number
    offset?: number
  } = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<McpKnowledgeJobList>(`/api/admin/mcp/knowledge/jobs${suffix}`)
  },
  mcpKnowledgeJob: (id: number) => request<McpKnowledgeJob>(`/api/admin/mcp/knowledge/jobs/${id}`),
  createMcpKnowledgeJobText: (payload: { kb_id: string; text: string; source_name?: string }) =>
    request<McpKnowledgeJob>('/api/admin/mcp/knowledge/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  createMcpKnowledgeJobFile: (kbId: string, file: File) => {
    const form = new FormData()
    form.set('kb_id', kbId)
    form.set('file', file)
    return request<McpKnowledgeJob>('/api/admin/mcp/knowledge/jobs/upload', { method: 'POST', body: form })
  },
  createMcpKnowledgeJobFiles: (kbId: string, files: File[]) => {
    const form = new FormData()
    form.set('kb_id', kbId)
    form.set(
      'relative_paths',
      JSON.stringify(
        files.map((file) => (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name),
      ),
    )
    files.forEach((file) => form.append('files', file, file.name))
    return request<McpKnowledgeBatchUpload>('/api/admin/mcp/knowledge/jobs/upload-batch', {
      method: 'POST',
      body: form,
    })
  },
  createMcpKnowledgeReembedJob: (payload: { kb_id: string; document_id: string }) =>
    request<McpKnowledgeJob>('/api/admin/mcp/knowledge/jobs/reembed', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  retryMcpKnowledgeJob: (id: number) =>
    request<McpKnowledgeJob>(`/api/admin/mcp/knowledge/jobs/${id}/retry`, { method: 'POST' }),
  cancelMcpKnowledgeJob: (id: number) =>
    request<McpKnowledgeJob>(`/api/admin/mcp/knowledge/jobs/${id}/cancel`, { method: 'POST' }),
  deleteMcpKnowledgeJob: (id: number) =>
    request<void>(`/api/admin/mcp/knowledge/jobs/${id}`, { method: 'DELETE' }),
  searchMcpKnowledge: (kbId: string, payload: { query: string; mode?: string; top_k?: number }) =>
    request<McpKnowledgeSearchResult>(`/api/admin/mcp/knowledge/bases/${encodeURIComponent(kbId)}/search`, {
      method: 'POST',
      body: JSON.stringify({ kb_id: kbId, ...payload }),
    }),
  mcpKeysForScope: () => request<McpKeyItem[]>('/api/admin/mcp/keys'),
  mcpCalls: (
    query: {
      limit?: number
      offset?: number
      capability_id?: string
      mcp_key_id?: number
      success?: boolean
      q?: string
    } = {},
  ) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value))
    })
    const suffix = params.toString() ? `?${params}` : ''
    return request<McpCallLogList>(`/api/admin/mcp/calls${suffix}`)
  },
}

export type DocParseJob = {
  job_id: string
  source_name: string
  source_ext: string
  source_size: number
  status: string
  stage: string
  percent: number
  message: string
  warnings: string[]
  error_message: string | null
  markdown_bytes: number
  page_count: number
  ingest_job_id: number | null
  download_ready: boolean
  markdown?: string
  truncated?: boolean
  created_at: string | null
  finished_at: string | null
}

export type DocParseJobList = {
  items: DocParseJob[]
  total: number
  counts?: Record<string, number>
}

function uploadDocParse(file: File, onProgress?: (percent: number) => void): Promise<DocParseJob> {
  const form = new FormData()
  form.set('file', file)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/admin/mcp/docparse/jobs')
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (event) => {
      if (!onProgress || !event.lengthComputable) return
      onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)))
    }
    xhr.onload = () => {
      if (xhr.status === 401) {
        clearToken()
        if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        let message = `请求失败 (${xhr.status})`
        try {
          const body = JSON.parse(xhr.responseText)
          const detail = body.detail
          message =
            (detail && detail.error && detail.error.message) ||
            (typeof detail === 'string' ? detail : '') ||
            body.message ||
            message
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, message))
        return
      }
      onProgress?.(100)
      resolve(JSON.parse(xhr.responseText) as DocParseJob)
    }
    xhr.onerror = () => reject(new ApiError(0, '上传失败'))
    xhr.send(form)
  })
}

export type McpSiteVersion = {
  id: string
  site_id: string
  version_no: number
  status: string
  stage: string
  percent: number
  message: string
  content_hash: string | null
  reused_version_id: string | null
  entry_file: string
  file_count: number
  total_bytes: number
  source_name: string
  error_message: string | null
  created_by: string
  is_current: boolean
  purged: boolean
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export type McpSite = {
  id: string
  slug: string
  name: string
  description: string
  access_mode: string
  has_token: boolean
  status: string
  entry_file: string
  spa_fallback: boolean
  current_version_id: string | null
  current_version_no: number | null
  preview_url: string
  total_bytes: number
  created_by: string
  mcp_key_id: number | null
  created_at: string | null
  updated_at: string | null
  versions?: McpSiteVersion[]
}

export type McpSiteList = { items: McpSite[]; total: number }

export type McpSiteDeployResult = { site: McpSite; version: McpSiteVersion; preview_url: string }

function uploadSite(
  path: string,
  file: File,
  fields: { slug?: string; name?: string; entry?: string; activate?: boolean },
  onProgress?: (percent: number) => void,
): Promise<McpSiteDeployResult> {
  const form = new FormData()
  form.set('file', file)
  if (fields.slug) form.set('slug', fields.slug)
  if (fields.name) form.set('name', fields.name)
  if (fields.entry) form.set('entry', fields.entry)
  if (fields.activate === false) form.set('activate', 'false')
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (event) => {
      if (!onProgress || !event.lengthComputable) return
      onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)))
    }
    xhr.onload = () => {
      if (xhr.status === 401) {
        clearToken()
        if (!window.location.pathname.startsWith('/login')) window.location.href = '/login'
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        let message = `请求失败 (${xhr.status})`
        try {
          const body = JSON.parse(xhr.responseText)
          const detail = body.detail
          message =
            (detail && detail.error && detail.error.message) ||
            (typeof detail === 'string' ? detail : '') ||
            body.message ||
            message
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, message))
        return
      }
      onProgress?.(100)
      resolve(JSON.parse(xhr.responseText) as McpSiteDeployResult)
    }
    xhr.onerror = () => reject(new ApiError(0, '上传失败'))
    xhr.send(form)
  })
}

export type McpCatalogItem = {
  capability_id: string
  name: string
  description: string
  version: string
  category: string
  status: string
  admin_path: string
  icon: string
  input_schema: Record<string, unknown>
  tools: { name: string; description: string; operation: string; input_schema: Record<string, unknown> }[]
}

export type McpKeyItem = {
  id: number
  name: string
  key_prefix: string
  status: string
  capability_ids: string[]
  created_at: string
  last_used_at: string | null
  key?: string | null
}

export type McpCapabilityIntegration = {
  rest_endpoints?: { method: string; path: string; summary?: string; content_type?: string }[]
  notes?: string[]
}

export type McpIntegrationCapability = {
  capability_id: string
  name: string
  description: string
  version: string
  category: string
  status: string
  admin_path: string
  icon: string
  input_schema: Record<string, unknown>
  integration: McpCapabilityIntegration
  authorized: boolean
  tools: { name: string; description: string; operation: string; input_schema: Record<string, unknown> }[]
}

export type McpKeyIntegration = {
  origin: string
  mcp_url: string
  rest_base_url: string
  key: {
    id: number
    name: string
    key_prefix: string
    status: string
    capability_ids: string[]
  }
  capabilities: McpIntegrationCapability[]
}

export type McpKnowledgeBase = {
  id: string
  name: string
  description: string
  scope: string
  document_count: number
  chunk_count: number
  stale_document_count: number
  signature_mismatch: boolean
  embedding_account_id: number | null
  embedding_account_name: string | null
  embedding_model: string | null
  embedding_dimensions: number | null
  allowed_mcp_key_ids: number[]
  active_job_count: number
  last_job_status: string | null
  created_at: string
  updated_at: string
}

export type McpKnowledgeJob = {
  id: number
  kb_id: string
  kb_name: string | null
  kind: string
  source_name: string
  content_size: number
  chunk_count: number
  status: string
  stage: string
  percent: number
  message: string
  processed_chunks: number
  total_chunks: number
  attempts: number
  max_attempts: number
  document_id: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type McpKnowledgeBatchUpload = {
  created: number
  job_ids: number[]
  skipped: { name: string; reason: string }[]
}

export type McpKnowledgeJobList = {
  items: McpKnowledgeJob[]
  total: number
  counts: Record<string, number>
}

export type McpKnowledgeEmbeddingAccount = {
  id: number
  name: string
  provider: string
  source: string
  available: boolean
  default_model: string | null
  models: string[]
}

export type McpKnowledgeDocument = {
  id: string
  kb_id: string
  source_name: string
  content_size: number
  chunk_count: number
  vector_status: string
  vector_error: string | null
  created_at: string
}

export type McpKnowledgeSearchResult = {
  kb_id: string | null
  kb_ids: string[]
  mode: string
  degraded: boolean
  degraded_reason: string | null
  hits: {
    chunk_id: string
    document_id: string
    kb_id: string
    text: string
    score: number
    source_name: string
  }[]
}

export type McpCallLogItem = {
  id: number
  created_at: string
  mcp_key_id: number | null
  mcp_key_name: string | null
  mcp_key_prefix: string | null
  capability_id: string
  operation: string
  success: boolean
  latency_ms: number
  error_message: string | null
}

export type McpCallLogList = {
  items: McpCallLogItem[]
  total: number
  limit: number
  offset: number
}

/** 语音 WebSocket 地址：dev 走 vite 代理，prod 同源。 */
export function voiceSocketUrl(path: string, token: string): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}${path}?token=${encodeURIComponent(token)}`
}

export const VOICE_DESKTOP_WS_PATH = '/api/voice/desktop/connect'


export type ContentAuditFinding = {
  id: number
  log_id: number
  message_seq: number
  category: string
  lexicon_category: string | null
  rule_key: string
  severity: string
  excerpt: string
  start_offset: number
  end_offset: number
  api_key_id: number | null
  api_key_name: string | null
  account_name: string | null
  created_at: string
}

export type ContentAuditFindingList = {
  items: ContentAuditFinding[]
  total: number
  page: number
  page_size: number
}

export type ContentAuditSummary = {
  running: boolean
  status: string
  paused: boolean
  scanned_in_run: number
  total_in_run: number
  last_finished_at: string | null
  last_message: string | null
  error_message: string | null
  scanned_count: number
  total_logs: number
  finding_count: number
  remaining: number
  processed: number | null
  new_findings: number | null
  lexicon_ok: boolean | null
  lexicon_updated_at: string | null
  lexicon_word_count: number
  by_category: Record<string, number>
  lexicon_categories: string[]
}

export type ContentAuditLexiconSync = {
  ok: boolean
  updated_at: string | null
  word_count: number
  categories: string[]
  error_message: string | null
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

// ---------------- 语音输入 ----------------

export type VoicePolishMode = 'off' | 'error_fix' | 'rewrite'

export type VoiceRoom = {
  id: number
  roomId: string
  name: string
  status: string
  joinCode: string
  requirePin: boolean
  note: string | null
  asrProvider: string
  asrModel: string
  disfluencyRemoval: boolean
  maxRecordingSeconds: number
  polishMode: VoicePolishMode
  polishAccountId: number | null
  polishModel: string | null
  polishSystemPrompt: string | null
  polishTemperature: number
  logPartials: boolean
  phoneTokenTtlSeconds?: number
  createdAt: string | null
  updatedAt: string | null
}

export type VoiceRoomInput = {
  name?: string
  pin?: string | null
  note?: string | null
  asrModel?: string
  disfluencyRemoval?: boolean
  maxRecordingSeconds?: number
  polishMode?: VoicePolishMode
  polishAccountId?: number | null
  polishModel?: string | null
  polishSystemPrompt?: string | null
  polishTemperature?: number
  logPartials?: boolean
}

export type VoiceRoomUpdateInput = VoiceRoomInput & { status?: string }

export type VoiceRoomSummary = VoiceRoom & {
  online: { busy: boolean; phones: number; desktops: number }
  counts: { clients: number; online: number; phones: number; desktops: number }
  todaySegments: number
}

export type VoiceSegment = {
  id: number
  segId: string
  sessionId: number | null
  seq: number
  rev: number
  state: string
  rawText: string
  polishedText: string | null
  polishStatus: string | null
  polishModel: string | null
  polishAccountId: number | null
  polishAccountName: string | null
  polishMs: number | null
  polishError: string | null
  asrBeginMs: number | null
  asrEndMs: number | null
  deliverCount: number
  ackCount: number
  endToEndMs: number | null
  createdAt: string | null
}

export type VoiceEvent = {
  id: number
  kind: string
  level: string
  message: string | null
  payload: Record<string, unknown> | null
  sessionId: number | null
  segmentId: number | null
  clientId: number | null
  createdAt: string | null
}

export type VoiceSession = {
  id: number
  sessionUid: string
  clientName: string | null
  status: string
  asrModel: string
  audioMs: number
  frameCount: number
  sentenceCount: number
  asrUsageSeconds: number | null
  firstPartialMs: number | null
  errorMessage: string | null
  startedAt: string | null
  endedAt: string | null
}

export type VoiceLive = {
  room: VoiceRoom
  state: { busy: boolean; phones: number; desktops: number }
  onlineClients: { clientUid: string; role: string; name: string }[]
  counts: { clients: number; online: number; phones: number; desktops: number }
  recentSegments: {
    segId: string
    seq: number
    rev: number
    state: string
    rawText: string
    polishedText: string | null
    polishStatus: string | null
    polishModel: string | null
    polishMs: number | null
    deliverCount: number
    ackCount: number
    createdAt: string | null
  }[]
  asrConfigured: boolean
  sessions: VoiceSession[]
}

export type VoicePolishAccount = {
  id: number
  name: string
  provider: string
  source: string
  available: boolean
  defaultModel: string
  models: string[]
}

export type VoicePolishTestResult = {
  status: string
  input: string
  output: string
  changed: boolean
  model: string | null
  accountId: number | null
  ms: number
  error: string | null
  reason: string | null
}

export type VoiceTokenResponse = {
  token: string
  roomId: string
  wsUrl: string
  expiresInSeconds: number
  desktopConfig: { serverUrl: string; roomId: string; token: string }
}

export type VoiceSessionList = {
  items: VoiceSession[]
  total: number
  page: number
  page_size: number
}

export type VoiceSegmentList = {
  items: VoiceSegment[]
  total: number
  page: number
  page_size: number
}

export type VoiceEventList = {
  items: VoiceEvent[]
  total: number
  page: number
  page_size: number
  kinds: string[]
}

export type VoiceJoinResponse = {
  token: string
  expiresInSeconds: number
  wsPath: string
  wsUrl: string
  room: { roomId: string; name: string; asrModel: string; maxRecordingSeconds: number; polishMode: VoicePolishMode }
}
