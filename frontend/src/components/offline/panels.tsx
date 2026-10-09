import { useEffect, useMemo, useRef, useState } from 'react'
import { Copy, HardDriveDownload } from 'lucide-react'
import { Badge, Button, Select } from '../ui'
import { notifyBad, notifyOk } from '../../lib/toast'
import { copyText, errorMessage } from '../../lib/utils'
import type {
  DockerManifest,
  DockerManifestList,
  DockerPlatform,
  MsStoreFile,
  OfflineApi,
} from '../../lib/offline'
import { EmptyNote, ErrorNote, HistoryInput, ResultCard } from './shared'
import { useHistory } from './useHistory'

type PanelProps = { api: OfflineApi; onQueued: () => void }

const CHROME_ID_RE = /[a-z]{32}/

/** 统一的「缓存到服务器」动作：入队后刷新缓存列表并在缓存卡片里看进度。 */
async function queueToServer(action: Promise<unknown>, onQueued: () => void) {
  try {
    await action
    notifyOk('已加入缓存队列，可在下方「已缓存」查看进度')
    onQueued()
  } catch (caught) {
    notifyBad(errorMessage(caught, '加入缓存失败'))
  }
}

function isDockerManifestList(value: DockerManifest | DockerManifestList): value is DockerManifestList {
  return (value as DockerManifestList).type === 'manifest_list'
}

/* ------------------------------ VSCode ------------------------------ */
export function VSCodePanel({ api, onQueued }: PanelProps) {
  const { history, remember } = useHistory('vscode')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<OfflineApi['vscodeQuery']>> | null>(null)
  const [version, setVersion] = useState('')

  async function submit() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const data = await api.vscodeQuery(query)
      setResult(data)
      setVersion(data.versions[0] ?? '')
      remember(query)
    } catch (caught) {
      setError(errorMessage(caught, '查询失败'))
    } finally {
      setBusy(false)
    }
  }

  const vsixUrl = result && version ? result.download_url_template.replace('{version}', version) : ''

  return (
    <div className="space-y-4">
      <HistoryInput
        value={query}
        onChange={setQuery}
        onSubmit={submit}
        placeholder="粘贴 Marketplace 插件页链接，例如 https://marketplace.visualstudio.com/items?itemName=ms-python.python"
        history={history}
        busy={busy}
        listId="offline-vscode-history"
      />
      {error ? <ErrorNote message={error} /> : null}
      {result ? (
        <ResultCard
          title={result.display_name}
          subtitle={result.item_name}
          actions={
            <>
              <Select value={version} onChange={(event) => setVersion(event.target.value)} className="w-40">
                {result.versions.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
              <Button
                type="button"
                disabled={!version || queuing}
                onClick={() => {
                  setQueuing(true)
                  void queueToServer(
                    api.vscodeCache({
                      publisher: result.publisher,
                      extension: result.extension,
                      version,
                      display_name: result.display_name,
                      filename: `${result.extension}-${version}.vsix`,
                    }),
                    onQueued,
                  ).finally(() => setQueuing(false))
                }}
              >
                <HardDriveDownload size={15} /> {queuing ? '提交中…' : '缓存到服务器'}
              </Button>
              <Button
                type="button"
                variant="line"
                onClick={() => {
                  void copyText(vsixUrl).then(() => notifyOk('已复制直链'))
                }}
              >
                <Copy size={15} /> 复制直链
              </Button>
            </>
          }
        />
      ) : null}
    </div>
  )
}

/* ------------------------------ Chrome ------------------------------ */
export function ChromePanel({ api, onQueued }: PanelProps) {
  const { history, remember } = useHistory('chrome')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuing, setQueuing] = useState('')
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState<{ id: string; name: string }[]>([])
  const [detail, setDetail] = useState<{ id: string; name?: string | null; description?: string | null } | null>(null)

  const idMatch = query.trim().toLowerCase().match(CHROME_ID_RE)
  const resolvedId = idMatch?.[0] ?? ''

  async function resolve(target: string) {
    setBusy(true)
    setError('')
    try {
      const data = await api.chromeDetail(target)
      setDetail(data)
      setSuggestions([])
      remember(target)
    } catch (caught) {
      setError(errorMessage(caught, '获取扩展信息失败'))
    } finally {
      setBusy(false)
    }
  }

  // 输入变化后 400ms 搜索建议（仅在不像 ID/链接时）
  useEffect(() => {
    const keyword = query.trim()
    if (keyword.length < 2 || CHROME_ID_RE.test(keyword.toLowerCase()) || /[./]/.test(keyword)) {
      setSuggestions([])
      return
    }
    const timer = setTimeout(() => {
      void api
        .chromeSearch(keyword)
        .then((data) => setSuggestions(data.results))
        .catch(() => setSuggestions([]))
    }, 400)
    return () => clearTimeout(timer)
  }, [api, query])

  return (
    <div className="space-y-4">
      <HistoryInput
        value={query}
        onChange={(value) => {
          setQuery(value)
          setDetail(null)
        }}
        onSubmit={() => resolvedId && resolve(resolvedId)}
        placeholder="扩展名称、32 位扩展 ID 或商店链接"
        history={history}
        busy={busy}
        listId="offline-chrome-history"
      />
      {suggestions.length ? (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setQuery(item.id)
                void resolve(item.id)
              }}
              className="rounded-full border border-line px-3 py-1.5 text-xs text-mist transition hover:border-signal/40 hover:text-paper"
            >
              {item.name}
            </button>
          ))}
        </div>
      ) : null}
      {error ? <ErrorNote message={error} /> : null}
      {detail ? (
        <ResultCard
          title={detail.name || detail.id}
          subtitle={detail.id}
          actions={
            <>
              {(['crx', 'zip'] as const).map((format) => (
                <Button
                  key={format}
                  type="button"
                  variant={format === 'crx' ? 'primary' : 'line'}
                  disabled={queuing !== ''}
                  onClick={() => {
                    setQueuing(format)
                    void queueToServer(
                      api.chromeCache({
                        id: detail.id,
                        format,
                        name: detail.name ?? '',
                        description: detail.description ?? '',
                      }),
                      onQueued,
                    ).finally(() => setQueuing(''))
                  }}
                >
                  <HardDriveDownload size={15} /> {queuing === format ? '提交中…' : `缓存 ${format.toUpperCase()}`}
                </Button>
              ))}
            </>
          }
        >
          {detail.description ? <p className="text-xs leading-5 text-mist">{detail.description}</p> : null}
        </ResultCard>
      ) : null}
    </div>
  )
}

/* ------------------------------ Edge ------------------------------ */
export function EdgePanel({ api, onQueued }: PanelProps) {
  const { history, remember } = useHistory('edge')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuing, setQueuing] = useState('')
  const [error, setError] = useState('')
  const [suggestions, setSuggestions] = useState<{ id: string; name: string; developer?: string }[]>([])
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)

  const looksResolvable = /^[a-z]{32}$/i.test(query.trim()) || /^[A-Za-z0-9]{12}$/.test(query.trim()) || /[./]/.test(query.trim())

  async function resolve(target: string) {
    setBusy(true)
    setError('')
    try {
      const data = await api.edgeDetail(target)
      setDetail(data)
      setSuggestions([])
      remember(target)
    } catch (caught) {
      setError(errorMessage(caught, '获取扩展信息失败'))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    const keyword = query.trim()
    if (keyword.length < 2 || looksResolvable) {
      setSuggestions([])
      return
    }
    const timer = setTimeout(() => {
      void api
        .edgeSearch(keyword)
        .then((data) => setSuggestions(data.results))
        .catch(() => setSuggestions([]))
    }, 400)
    return () => clearTimeout(timer)
  }, [api, query, looksResolvable])

  const detailId = typeof detail?.crxId === 'string' ? detail.crxId : ''
  const detailName = typeof detail?.name === 'string' ? detail.name : detailId

  return (
    <div className="space-y-4">
      <HistoryInput
        value={query}
        onChange={(value) => {
          setQuery(value)
          setDetail(null)
        }}
        onSubmit={() => resolve(query)}
        placeholder="名称、CRX ID、ProductId 或 Edge 加载项链接"
        history={history}
        busy={busy}
        listId="offline-edge-history"
      />
      {suggestions.length ? (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setQuery(item.id)
                void resolve(item.id)
              }}
              className="rounded-full border border-line px-3 py-1.5 text-xs text-mist transition hover:border-signal/40 hover:text-paper"
            >
              {item.name}
              {item.developer ? <span className="ml-1 text-mist/60">· {item.developer}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
      {error ? <ErrorNote message={error} /> : null}
      {detail && detailId ? (
        <ResultCard
          title={detailName}
          subtitle={detailId}
          actions={
            <>
              {(['crx', 'zip'] as const).map((format) => (
                <Button
                  key={format}
                  type="button"
                  variant={format === 'crx' ? 'primary' : 'line'}
                  disabled={queuing !== ''}
                  onClick={() => {
                    setQueuing(format)
                    void queueToServer(
                      api.edgeCache({
                        id: detailId,
                        format,
                        name: detailName,
                        description: typeof detail.shortDescription === 'string' ? detail.shortDescription : '',
                        icon_url: typeof detail.logoUrl === 'string' ? detail.logoUrl : typeof detail.iconUrl === 'string' ? detail.iconUrl : '',
                      }),
                      onQueued,
                    ).finally(() => setQueuing(''))
                  }}
                >
                  <HardDriveDownload size={15} /> {queuing === format ? '提交中…' : `缓存 ${format.toUpperCase()}`}
                </Button>
              ))}
            </>
          }
        >
          {typeof detail.shortDescription === 'string' ? (
            <p className="text-xs leading-5 text-mist">{detail.shortDescription}</p>
          ) : typeof detail.description === 'string' ? (
            <p className="text-xs leading-5 text-mist">{detail.description}</p>
          ) : null}
        </ResultCard>
      ) : null}
    </div>
  )
}

/* ------------------------------ Docker ------------------------------ */
function platformLabel(platform: DockerPlatform) {
  return [platform.os, platform.architecture, platform.variant].filter(Boolean).join('/')
}

/** 用选中的标签替换引用里的 tag（形如 nginx / library/nginx:old）。 */
function withTag(reference: string, tag: string) {
  const base = reference.split('@')[0]
  const lastColon = base.lastIndexOf(':')
  const lastSlash = base.lastIndexOf('/')
  if (lastColon > lastSlash) return `${base.slice(0, lastColon)}:${tag}`
  return `${base}:${tag}`
}

export function DockerPanel({ api, onQueued }: PanelProps) {
  const { history, remember } = useHistory('docker')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [error, setError] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [tag, setTag] = useState('')
  const [platforms, setPlatforms] = useState<DockerPlatform[]>([])
  const [platform, setPlatform] = useState('')
  const [ready, setReady] = useState(false)
  const requestRef = useRef(0)

  async function loadManifest(target: string, selectedPlatform: string) {
    const token = (await api.dockerAuth(target)).token
    if (!token) throw new Error('未获取到认证令牌')
    return api.dockerManifest(target, token, selectedPlatform || undefined)
  }

  async function submit() {
    const requestId = ++requestRef.current
    setBusy(true)
    setError('')
    setReady(false)
    setPlatforms([])
    setPlatform('')
    try {
      const target = query.trim()
      const tagData = await api.dockerTags(target)
      const names = (tagData.results ?? []).map((item) => item.name)
      if (requestId !== requestRef.current) return
      if (!names.length) {
        const search = await api.dockerSearch(target)
        const found = (search.results ?? [])[0]?.repo_name
        if (found) {
          setQuery(`${found}:latest`)
          throw new Error(`未找到该镜像，已为你填入候选：${found}:latest`)
        }
        throw new Error('未找到该镜像的标签')
      }
      const initialTag = names[0]
      setTags(names)
      setTag(initialTag)

      const manifest = await loadManifest(withTag(target, initialTag), '')
      if (requestId !== requestRef.current) return
      if (isDockerManifestList(manifest)) {
        const list = manifest.platforms
        setPlatforms(list)
        const preferred = list.find((item) => platformLabel(item) === 'linux/amd64') ?? list[0]
        setPlatform(preferred ? platformLabel(preferred) : '')
      }
      setReady(true)
      remember(target)
    } catch (caught) {
      if (requestId === requestRef.current) setError(errorMessage(caught, '解析镜像失败'))
    } finally {
      if (requestId === requestRef.current) setBusy(false)
    }
  }

  async function changeTag(next: string) {
    setTag(next)
    setError('')
    try {
      const manifest = await loadManifest(withTag(query.trim(), next), platform)
      if (isDockerManifestList(manifest)) {
        const list = manifest.platforms
        setPlatforms(list)
        const preferred = list.find((item) => platformLabel(item) === 'linux/amd64') ?? list[0]
        setPlatform(preferred ? platformLabel(preferred) : '')
      }
    } catch (caught) {
      setError(errorMessage(caught, '切换标签失败'))
    }
  }

  async function changePlatform(next: string) {
    setPlatform(next)
    setError('')
    try {
      await loadManifest(withTag(query.trim(), tag), next)
    } catch (caught) {
      setError(errorMessage(caught, '切换架构失败'))
    }
  }

  const packageFilename = `${withTag(query.trim(), tag).replace(/[/:]/g, '_')}.tar`

  return (
    <div className="space-y-4">
      <HistoryInput
        value={query}
        onChange={setQuery}
        onSubmit={submit}
        placeholder="nginx:latest / library/nginx / Docker Hub 链接"
        history={history}
        busy={busy}
        listId="offline-docker-history"
      />
      {error ? <ErrorNote message={error} /> : null}
      {tags.length ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
          <span>标签</span>
          <Select value={tag} onChange={(event) => void changeTag(event.target.value)} className="w-40">
            {tags.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
          {platforms.length ? (
            <>
              <span className="ml-2">架构</span>
              <Select value={platform} onChange={(event) => void changePlatform(event.target.value)} className="w-40">
                {platforms.map((item) => (
                  <option key={platformLabel(item)} value={platformLabel(item)}>
                    {platformLabel(item)}
                  </option>
                ))}
              </Select>
            </>
          ) : null}
        </div>
      ) : null}
      {ready ? (
        <ResultCard
          title={withTag(query.trim(), tag)}
          subtitle={platform ? `目标架构 ${platform}` : '单一架构'}
          actions={
            <Button
              type="button"
              disabled={queuing}
              onClick={() => {
                setQueuing(true)
                void queueToServer(
                  api.dockerCache({
                    query: withTag(query.trim(), tag),
                    platform: platform || undefined,
                    filename: packageFilename,
                  }),
                  onQueued,
                ).finally(() => setQueuing(false))
              }}
            >
              <HardDriveDownload size={15} /> {queuing ? '提交中…' : '缓存到服务器'}
            </Button>
          }
        >
          <p className="text-xs leading-5 text-mist">服务器在后台拉取各层并打包为 docker load 兼容的 .tar，完成后可在「已缓存」下载。</p>
        </ResultCard>
      ) : null}
    </div>
  )
}

/* ------------------------------ Microsoft Store ------------------------------ */
function detectMsStoreType(query: string): string {
  const trimmed = query.trim()
  if (/^https?:\/\//i.test(trimmed) || /apps\.microsoft\.com|microsoft\.com\/.*store/i.test(trimmed)) return 'url'
  if (/^[A-Za-z0-9]{12}$/.test(trimmed)) return 'ProductId'
  if (/^[A-Za-z0-9][A-Za-z0-9._-]*_[A-Za-z0-9]+$/.test(trimmed)) return 'PackageFamilyName'
  if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(trimmed)) return 'CategoryId'
  return 'url'
}

export function MsStorePanel({ api, onQueued }: PanelProps) {
  const { history, remember } = useHistory('msstore')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [queuing, setQueuing] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<OfflineApi['msstoreResolve']>> | null>(null)

  const files = useMemo(
    () => (result?.files ?? []).filter((file) => !file.name.toLowerCase().endsWith('.blockmap')),
    [result],
  )

  async function submit() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const data = await api.msstoreResolve(detectMsStoreType(query), query.trim())
      setResult(data)
      remember(query)
    } catch (caught) {
      setError(errorMessage(caught, '解析失败'))
    } finally {
      setBusy(false)
    }
  }

  function cacheFile(file: MsStoreFile) {
    setQueuing(file.url)
    void queueToServer(
      api.msstoreCache({ url: file.url, filename: file.name, title: result?.title ?? file.name }),
      onQueued,
    ).finally(() => setQueuing(''))
  }

  return (
    <div className="space-y-4">
      <HistoryInput
        value={query}
        onChange={setQuery}
        onSubmit={submit}
        placeholder="商店链接 / ProductId / PackageFamilyName / CategoryId"
        history={history}
        busy={busy}
        listId="offline-msstore-history"
      />
      {error ? <ErrorNote message={error} /> : null}
      {result ? (
        <ResultCard title={result.title || result.productId} subtitle={result.publisherName}>
          {result.description ? <p className="text-xs leading-5 text-mist">{result.description}</p> : null}
          <div className="mt-3 space-y-2">
            {files.length ? (
              files.map((file) => (
                <div key={file.url} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line/70 bg-ink/40 px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs text-paper">{file.name}</div>
                    <div className="text-[11px] text-mist">{file.size || '未知大小'}</div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="line"
                      onClick={() => {
                        void copyText(file.url).then(() => notifyOk('已复制真实下载地址'))
                      }}
                    >
                      <Copy size={14} /> 复制
                    </Button>
                    <Button type="button" disabled={queuing !== ''} onClick={() => cacheFile(file)}>
                      <HardDriveDownload size={14} /> {queuing === file.url ? '提交中…' : '缓存到服务器'}
                    </Button>
                  </div>
                </div>
              ))
            ) : (
              <EmptyNote message={result.filesError || '未获取到可下载的安装包'} />
            )}
          </div>
          {result.packageFamilyNames?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {result.packageFamilyNames.map((name) => (
                <Badge key={name} tone="mist">
                  {name}
                </Badge>
              ))}
            </div>
          ) : null}
        </ResultCard>
      ) : null}
    </div>
  )
}
