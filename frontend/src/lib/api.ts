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
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(path, { ...init, headers })
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
  auth_type: string
  base_url: string
  status: string
  has_credential: boolean
  api_key?: string | null
  last_probe_ok: boolean | null
  last_probe_latency_ms: number | null
  last_probe_message: string | null
  last_probe_at: string | null
  quota: AccountQuota | null
  quota_updated_at: string | null
  models: string[]
  models_updated_at: string | null
  oauth_expires_at: string | null
  created_at: string
}

export type CcSwitchTarget = {
  app: string
  label: string
  needs_dialog: boolean
  url?: string
}

export type ShareLookup = {
  name: string
  account_name: string
  provider: string
  provider_label: string
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
}

export type ApiKeyItem = {
  id: number
  name: string
  key_prefix: string
  key?: string | null
  account_id: number
  account_name: string
  provider: string
  status: string
  created_at: string
  last_used_at: string | null
}

export type LogItem = {
  id: number
  account_id: number
  account_name?: string
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
  providers: () => request<Provider[]>('/api/admin/providers'),
  accounts: () => request<Account[]>('/api/admin/accounts'),
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
  keys: () => request<ApiKeyItem[]>('/api/admin/keys'),
  createKey: (payload: { name: string; account_id: number }) =>
    request<ApiKeyItem>('/api/admin/keys', { method: 'POST', body: JSON.stringify(payload) }),
  key: (id: number) => request<ApiKeyItem>(`/api/admin/keys/${id}`),
  ccSwitch: (id: number) =>
    request<{ display_name: string; models: string[]; targets: CcSwitchTarget[] }>(`/api/admin/keys/${id}/cc-switch`),
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
}
