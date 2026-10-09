import { ApiError, getToken } from './api'

export type OfflineProvider = {
  slug: string
  label: string
  short_label: string
  description: string
  placeholder: string
}

export type VscodeResult = {
  publisher: string
  extension: string
  item_name: string
  display_name: string
  versions: string[]
  download_url_template: string
}

export type ChromeSearchItem = { id: string; name: string }
export type ChromeDetail = { id: string; name?: string | null; description?: string | null }

export type EdgeSearchItem = {
  id: string
  storeProductId?: string
  name: string
  developer?: string
  description?: string
  iconUrl?: string
}

export type DockerPlatform = { os: string; architecture: string; variant?: string; digest: string }
export type DockerManifestList = { type: 'manifest_list'; platforms: DockerPlatform[] }
export type DockerManifest = {
  mediaType?: string
  config?: { digest: string; size?: number }
  layers?: { digest: string; size?: number; mediaType?: string }[]
}

export type MsStoreFile = {
  url: string
  name: string
  expires?: string
  sha1?: string
  size?: string
}

export type MsStoreResult = {
  productId: string
  title: string
  publisherName: string
  description: string
  lastModifiedDate?: string
  packageFamilyNames?: string[]
  categoryId?: string | null
  files?: MsStoreFile[] | null
  filesError?: string | null
  skus?: unknown[]
}

export type OfflineCacheStatus = 'queued' | 'caching' | 'ready' | 'failed'

export type OfflineCacheItem = {
  id: number
  provider: string
  title: string
  subtitle: string
  description: string
  has_icon: boolean
  filename: string
  content_type: string
  size_bytes: number
  source: string
  hit_count: number
  status: OfflineCacheStatus
  stage: string
  percent: number
  message: string
  error_message: string | null
  bytes_downloaded: number
  expected_bytes: number
  downloadable: boolean
  created_at: string | null
  updated_at: string | null
  last_accessed_at: string | null
}

export type VscodeCacheInput = {
  publisher: string
  extension: string
  version: string
  display_name?: string
  filename?: string
}

export type ExtensionCacheInput = {
  id: string
  format: 'crx' | 'zip'
  name?: string
  description?: string
  icon_url?: string
}

export type DockerCacheInput = {
  query: string
  platform?: string
  filename?: string
}

export type MsStoreCacheInput = {
  url: string
  filename?: string
  title?: string
}

export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const value = bytes / 1024 ** index
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  // 管理端接口需要管理员 JWT；公开端走门禁 Cookie（同源自动携带）
  if (path.startsWith('/api/admin')) {
    const token = getToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }
  const response = await fetch(path, { ...init, headers })
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
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

const enc = encodeURIComponent

/** 离线下载接口客户端；base 决定走管理端还是公开端（同一套路由）。 */
export function createOfflineApi(base: string) {
  const url = (path: string) => `${base}${path}`
  return {
    providers: () => requestJson<{ providers: OfflineProvider[] }>(url('/providers')),

    vscodeQuery: (query: string) =>
      requestJson<VscodeResult>(url('/vscode/query'), {
        method: 'POST',
        body: JSON.stringify({ query }),
      }),

    chromeSearch: (q: string) =>
      requestJson<{ results: ChromeSearchItem[] }>(`${url('/chrome/search')}?q=${enc(q)}`),
    chromeDetail: (id: string) =>
      requestJson<ChromeDetail>(`${url('/chrome/detail')}?id=${enc(id)}`),
    chromeCache: (input: ExtensionCacheInput) =>
      requestJson<{ item: OfflineCacheItem }>(url('/chrome/cache'), {
        method: 'POST',
        body: JSON.stringify(input),
      }),

    edgeSearch: (q: string) =>
      requestJson<{ results: EdgeSearchItem[] }>(`${url('/edge/search')}?q=${enc(q)}`),
    edgeDetail: (query: string) =>
      requestJson<Record<string, unknown>>(`${url('/edge/detail')}?query=${enc(query)}`),
    edgeCache: (input: ExtensionCacheInput) =>
      requestJson<{ item: OfflineCacheItem }>(url('/edge/cache'), {
        method: 'POST',
        body: JSON.stringify(input),
      }),

    dockerTags: (query: string) => requestJson<{ results?: { name: string }[] }>(`${url('/docker/tags')}?query=${enc(query)}`),
    dockerSearch: (q: string) =>
      requestJson<{ results?: { repo_name?: string; short_description?: string }[] }>(
        `${url('/docker/search')}?q=${enc(q)}&page_size=5`,
      ),
    dockerAuth: (query: string) => requestJson<{ token?: string }>(`${url('/docker/auth')}?query=${enc(query)}`),
    dockerManifest: (query: string, token: string, platform?: string) =>
      requestJson<DockerManifest | DockerManifestList>(
        `${url('/docker/manifest')}?query=${enc(query)}&token=${enc(token)}${platform ? `&platform=${enc(platform)}` : ''}`,
      ),
    dockerCache: (input: DockerCacheInput) =>
      requestJson<{ item: OfflineCacheItem }>(url('/docker/cache'), {
        method: 'POST',
        body: JSON.stringify(input),
      }),

    msstoreResolve: (type: string, query: string) =>
      requestJson<MsStoreResult>(
        `${url('/msstore/resolve')}?type=${type}&query=${enc(query)}&market=US&language=en-us`,
      ),
    msstoreCache: (input: MsStoreCacheInput) =>
      requestJson<{ item: OfflineCacheItem }>(url('/msstore/cache'), {
        method: 'POST',
        body: JSON.stringify(input),
      }),

    vscodeCache: (input: VscodeCacheInput) =>
      requestJson<{ item: OfflineCacheItem }>(url('/vscode/cache'), {
        method: 'POST',
        body: JSON.stringify(input),
      }),

    cache: (limit = 100) =>
      requestJson<{ items: OfflineCacheItem[]; total_bytes: number }>(`${url('/cache')}?limit=${limit}`),
    cacheDownloadUrl: (id: number) => `${url(`/cache/${id}/download`)}`,
    // 图标走免鉴权公开路由，<img> 无需携带令牌
    cacheIconUrl: (id: number) => `/api/public/offline/cache/${id}/icon`,
    deleteCache: (id: number) => requestJson<{ ok: boolean }>(`${url(`/cache/${id}`)}`, { method: 'DELETE' }),
    refreshCache: (id: number) =>
      requestJson<{ item: OfflineCacheItem }>(`${url(`/cache/${id}/refresh`)}`, { method: 'POST' }),

    /** 统一下载入口（自动处理管理端鉴权）。 */
    download: (target: string, filename?: string) => downloadWithAuth(base, target, filename),
  }
}

export type OfflineApi = ReturnType<typeof createOfflineApi>

/** 触发浏览器下载；同源代理走 cookie，跨域 https 直链直接打开。 */
export function triggerDownload(url: string, filename?: string) {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noopener'
  if (filename) anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

/**
 * 统一下载入口：公开端直接走链接（Cookie 鉴权、浏览器流式下载）；
 * 管理端带 JWT，锚点无法携带请求头，改为 fetch 取回 blob 再落地。
 */
export async function downloadWithAuth(base: string, url: string, filename?: string) {
  if (!base.startsWith('/api/admin')) {
    triggerDownload(url, filename)
    return
  }
  const token = getToken()
  const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!response.ok) throw new ApiError(response.status, '下载失败')
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  triggerDownload(objectUrl, filename)
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}
