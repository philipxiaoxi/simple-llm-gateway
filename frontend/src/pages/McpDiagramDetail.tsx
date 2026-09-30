import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  FileJson,
  Pencil,
  Plus,
  RefreshCw,
  Shapes,
  Trash2,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type McpDiagramSource, type McpSite } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

const VERSION_LABELS: Record<string, string> = {
  unpacking: '处理中',
  ready: '已就绪',
  failed: '失败',
  duplicate: '已复用',
}

const VERSION_TONES: Record<string, string> = {
  unpacking: 'border-signal/50 text-signal',
  ready: 'border-ok/50 text-ok',
  failed: 'border-danger/50 text-danger',
  duplicate: 'border-warn/50 text-warn',
}

const DIAGRAM_TYPES = ['architecture', 'workflow', 'sequence', 'dataflow', 'lifecycle'] as const
const QUALITIES = ['showcase', 'standard'] as const

type DiagramType = (typeof DIAGRAM_TYPES)[number]
type Quality = (typeof QUALITIES)[number]

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/

export function McpDiagramDetailPage() {
  const { siteId = '' } = useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [issuedToken, setIssuedToken] = useState('')
  const [frameKey, setFrameKey] = useState(0)
  const [previewing, setPreviewing] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [newVersionOpen, setNewVersionOpen] = useState(false)
  const [newVersionSeed, setNewVersionSeed] = useState<McpDiagramSource | null>(null)
  const [sourceVersion, setSourceVersion] = useState<number | null>(null)

  const diagram = useQuery({
    queryKey: ['mcp-diagram', siteId],
    queryFn: () => api.mcpDiagram(siteId),
    refetchInterval: (query) => {
      const active = query.state.data?.versions?.some((item) => item.status === 'unpacking')
      return active ? 1500 : 15000
    },
  })

  const diagramToken = useQuery({
    queryKey: ['mcp-diagram-token', siteId],
    queryFn: () => api.revealMcpDiagramToken(siteId),
    enabled: Boolean(diagram.data && diagram.data.access_mode === 'token' && diagram.data.has_token),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['mcp-diagram', siteId] })

  const activate = useMutation({
    mutationFn: (versionId: string) => api.activateMcpDiagramVersion(siteId, versionId),
    onSuccess: async () => {
      notifyOk('已切换当前版本')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '切换失败')),
  })

  const update = useMutation({
    mutationFn: (payload: Parameters<typeof api.updateMcpDiagram>[1]) => api.updateMcpDiagram(siteId, payload),
    onSuccess: async () => {
      setIssuedToken('')
      await invalidate()
      await queryClient.invalidateQueries({ queryKey: ['mcp-diagram-token', siteId] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  const generateToken = useMutation({
    mutationFn: () => api.generateMcpDiagramToken(siteId),
    onSuccess: async (result) => {
      setIssuedToken(result.token)
      notifyOk('已生成访问令牌')
      await invalidate()
      await queryClient.invalidateQueries({ queryKey: ['mcp-diagram-token', siteId] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '生成失败')),
  })

  const removeDiagram = useMutation({
    mutationFn: () => api.deleteMcpDiagram(siteId),
    onSuccess: async () => {
      notifyOk('图表已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-diagrams'] })
      navigate('/mcp-plaza/diagrams')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  if (diagram.isError) {
    return <div className="text-sm text-danger">{errorMessage(diagram.error, '加载失败')}</div>
  }
  if (!diagram.data) {
    return <div className="text-sm text-mist">加载中…</div>
  }

  const data = diagram.data
  const versions = data.versions ?? []
  const current = versions.find((item) => item.is_current)
  const gateToken = issuedToken || diagramToken.data?.token || ''
  const accessUrl =
    data.access_mode === 'token' && gateToken
      ? `${data.preview_url}?token=${encodeURIComponent(gateToken)}`
      : data.preview_url

  async function copyAccessUrl() {
    try {
      await navigator.clipboard.writeText(accessUrl)
      notifyOk('已复制访问链接')
    } catch {
      notifyBad('复制失败，请手动选择复制')
    }
  }

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza/diagrams" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回图表列表
      </Link>

      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-xl font-semibold">
              <Shapes size={20} className="text-signal" />
              {data.name || data.slug}
            </h2>
            {data.description ? (
              <p className="mt-1 break-words text-sm text-mist [overflow-wrap:anywhere]">{data.description}</p>
            ) : null}
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-mist">
              <span className="rounded-full border border-line px-2 py-0.5">/{data.slug}</span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {data.status === 'active' ? '启用' : '已停用'}
              </span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {data.access_mode === 'token' ? '令牌保护' : '公开'}
              </span>
              {current?.diagram_type ? (
                <span className="rounded-full border border-line px-2 py-0.5">{current.diagram_type}</span>
              ) : null}
              {current?.quality ? (
                <span className="rounded-full border border-line px-2 py-0.5">{current.quality}</span>
              ) : null}
              {data.current_version_no ? (
                <span className="rounded-full border border-line px-2 py-0.5">当前 v{data.current_version_no}</span>
              ) : null}
            </div>
            <a
              href={accessUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-block break-all text-sm text-signal hover:underline [overflow-wrap:anywhere]"
            >
              {accessUrl}
            </a>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button
              type="button"
              onClick={() => {
                setNewVersionSeed(null)
                setNewVersionOpen(true)
              }}
            >
              <Plus size={15} /> 新建版本
            </Button>
            <Button type="button" variant="line" onClick={() => setEditOpen(true)}>
              <Pencil size={14} /> 编辑信息
            </Button>
            <Button
              type="button"
              variant="line"
              disabled={update.isPending}
              onClick={() => update.mutate({ status: data.status === 'active' ? 'disabled' : 'active' })}
            >
              {data.status === 'active' ? '停用' : '启用'}
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={removeDiagram.isPending}
              onClick={() => {
                if (window.confirm(`删除图表「${data.name || data.slug}」及其全部版本？`)) removeDiagram.mutate()
              }}
            >
              <Trash2 size={14} /> 删除图表
            </Button>
          </div>
        </div>
      </Card>

      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <h3 className="text-sm font-medium text-paper">访问控制</h3>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant={data.access_mode === 'public' ? 'primary' : 'line'}
            disabled={update.isPending}
            onClick={() => update.mutate({ access_mode: 'public' })}
          >
            公开访问
          </Button>
          <Button
            type="button"
            variant={data.access_mode === 'token' ? 'primary' : 'line'}
            disabled={update.isPending}
            onClick={() => update.mutate({ access_mode: 'token' })}
          >
            令牌保护
          </Button>
          <Button
            type="button"
            variant="line"
            disabled={generateToken.isPending}
            onClick={() => generateToken.mutate()}
          >
            {data.has_token ? '重置令牌' : '生成令牌'}
          </Button>
        </div>
        {issuedToken ? (
          <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
            访问令牌（仅本次展示）：
            <span className="ml-1 break-all font-mono [overflow-wrap:anywhere]">{issuedToken}</span>
            <br />
            下方预览与「复制链接」都会自动带上该令牌。
          </div>
        ) : null}
      </Card>

      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-paper">图表预览</h3>
            <p className="mt-0.5 text-xs text-mist">
              {data.access_mode === 'token'
                ? gateToken
                  ? '已带上访问令牌，点击「开始预览」后在下方 iframe 内查看。'
                  : '令牌模式：生成令牌后即可预览。'
                : '公开访问，点击「开始预览」后在下方 iframe 内查看。'}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button type="button" variant="line" onClick={copyAccessUrl}>
              <Copy size={14} /> 复制链接
            </Button>
            <a
              href={accessUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md border border-line bg-panel-2 px-3 py-2 text-sm font-medium text-paper transition hover:border-mist/40 md:min-h-9"
            >
              <ExternalLink size={14} /> 新标签页打开
            </a>
            {previewing ? (
              <>
                <Button type="button" variant="line" onClick={() => setFrameKey((value) => value + 1)}>
                  <RefreshCw size={14} /> 刷新
                </Button>
                <Button type="button" variant="line" onClick={() => setPreviewing(false)}>
                  <EyeOff size={14} /> 关闭预览
                </Button>
              </>
            ) : null}
          </div>
        </div>
        <div className="break-all rounded-md border border-line bg-ink/60 px-3 py-2 text-xs text-mist [overflow-wrap:anywhere]">
          {accessUrl}
        </div>
        {data.current_version_id ? (
          previewing ? (
            <div className="overflow-hidden rounded-lg border border-line bg-white">
              <iframe
                key={frameKey}
                title="图表预览"
                src={accessUrl}
                className="h-[68vh] min-h-[420px] w-full"
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
              />
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-line px-4 py-10 text-center">
              <p className="text-sm text-mist">预览默认关闭，点击「开始预览」后再加载页面。</p>
              <Button type="button" onClick={() => setPreviewing(true)}>
                <Eye size={15} /> 开始预览
              </Button>
            </div>
          )
        ) : (
          <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center text-sm text-mist">
            图表尚未就绪，创建版本后即可预览。
          </div>
        )}
      </Card>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-paper">版本历史</h3>
        {versions.map((version) => {
          const canActivate = version.status === 'ready' && !version.is_current && !version.purged
          return (
            <Card key={version.id} className="min-w-0 space-y-2 overflow-hidden p-3">
              <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-paper">v{version.version_no}</span>
                    <span
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-xs',
                        VERSION_TONES[version.status] || 'border-line text-mist',
                      )}
                    >
                      {VERSION_LABELS[version.status] || version.status}
                    </span>
                    {version.is_current ? (
                      <span className="rounded-full bg-signal/15 px-2 py-0.5 text-xs text-signal">当前版本</span>
                    ) : null}
                    {version.diagram_type ? (
                      <span className="rounded-full border border-line px-2 py-0.5 text-xs text-mist">
                        {version.diagram_type}
                      </span>
                    ) : null}
                    {version.quality ? (
                      <span className="rounded-full border border-line px-2 py-0.5 text-xs text-mist">
                        {version.quality}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 break-words text-xs text-mist [overflow-wrap:anywhere]">
                    创建 {version.created_at ? formatTime(version.created_at) : '—'}
                    {version.finished_at ? ` · 完成 ${formatTime(version.finished_at)}` : ''}
                    {version.error_message ? ` · ${version.error_message}` : ''}
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="line"
                    onClick={() => setSourceVersion(version.version_no)}
                  >
                    <FileJson size={14} /> 查看源
                  </Button>
                  {canActivate ? (
                    <Button
                      type="button"
                      variant="line"
                      disabled={activate.isPending}
                      onClick={() => activate.mutate(version.id)}
                    >
                      设为当前
                    </Button>
                  ) : null}
                </div>
              </div>
            </Card>
          )
        })}
        {!versions.length ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center text-sm text-mist">
            还没有版本，点击「新建版本」粘贴 JSON 源。
          </div>
        ) : null}
      </div>

      {editOpen ? (
        <EditDiagramDialog
          site={data}
          pending={update.isPending}
          onClose={() => (update.isPending ? null : setEditOpen(false))}
          onSubmit={(payload) =>
            update.mutate(payload, {
              onSuccess: async () => {
                setEditOpen(false)
                notifyOk('图表信息已更新')
                await queryClient.invalidateQueries({ queryKey: ['mcp-diagrams'] })
              },
            })
          }
        />
      ) : null}

      {newVersionOpen ? (
        <NewVersionDialog
          slug={data.slug}
          seed={newVersionSeed}
          onClose={() => {
            setNewVersionOpen(false)
            setNewVersionSeed(null)
          }}
          onCreated={async () => {
            setNewVersionOpen(false)
            setNewVersionSeed(null)
            notifyOk('已渲染并新增版本')
            await invalidate()
          }}
        />
      ) : null}

      {sourceVersion !== null ? (
        <SourceDialog
          siteId={siteId}
          versionNo={sourceVersion}
          onClose={() => setSourceVersion(null)}
          onReuse={(source) => {
            setSourceVersion(null)
            setNewVersionSeed(source)
            setNewVersionOpen(true)
          }}
        />
      ) : null}
    </div>
  )
}

export default McpDiagramDetailPage

type DiagramEditPayload = Parameters<typeof api.updateMcpDiagram>[1]

function EditDiagramDialog({
  site,
  pending,
  onClose,
  onSubmit,
}: {
  site: McpSite
  pending: boolean
  onClose: () => void
  onSubmit: (payload: DiagramEditPayload) => void
}) {
  const [name, setName] = useState(site.name)
  const [slug, setSlug] = useState(site.slug)
  const [description, setDescription] = useState(site.description)

  const slugTouched = slug !== site.slug
  const slugInvalid = slugTouched && !SLUG_RE.test(slug)

  function submit() {
    if (slugInvalid) return
    const payload: DiagramEditPayload = {}
    if (name !== site.name) payload.name = name
    if (slugTouched) payload.slug = slug
    if (description !== site.description) payload.description = description
    if (!Object.keys(payload).length) {
      onClose()
      return
    }
    onSubmit(payload)
  }

  return (
    <Dialog title="编辑图表信息" onClose={onClose}>
      <div className="space-y-3">
        <Field label="显示名称">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="图表名称" disabled={pending} />
        </Field>
        <Field label="访问路径 (slug)">
          <Input
            value={slug}
            onChange={(event) => setSlug(event.target.value.trim().toLowerCase())}
            placeholder="my-diagram"
            disabled={pending}
          />
        </Field>
        {slugInvalid ? (
          <p className="text-xs text-danger">只允许小写字母、数字与中划线，长度不超过 64</p>
        ) : (
          <p className="text-xs text-mist">
            预览地址：<span className="break-all font-mono [overflow-wrap:anywhere]">{`/sites/${slug || '…'}/`}</span>
            {slugTouched ? <span className="text-warn"> · 改动后旧地址立即失效</span> : null}
          </p>
        )}
        <Field label="描述（可选）">
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="这个图表是做什么的"
            disabled={pending}
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-base text-paper outline-none placeholder:text-mist/70 focus:border-signal/70 md:text-sm"
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending || slugInvalid} onClick={submit}>
            {pending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function asDiagramType(value: string | null | undefined): DiagramType {
  return DIAGRAM_TYPES.includes(value as DiagramType) ? (value as DiagramType) : 'architecture'
}

function asQuality(value: string | null | undefined): Quality {
  return QUALITIES.includes(value as Quality) ? (value as Quality) : 'showcase'
}

function NewVersionDialog({
  slug,
  seed,
  onClose,
  onCreated,
}: {
  slug: string
  seed: McpDiagramSource | null
  onClose: () => void
  onCreated: () => void | Promise<void>
}) {
  const [diagramType, setDiagramType] = useState<DiagramType>(asDiagramType(seed?.type))
  const [quality, setQuality] = useState<Quality>(asQuality(seed?.quality))
  const [sourceText, setSourceText] = useState(seed ? JSON.stringify(seed.source, null, 2) : '')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () => {
      let parsed: unknown
      try {
        parsed = JSON.parse(sourceText)
      } catch {
        throw new Error('图表源不是合法 JSON')
      }
      return api.createMcpDiagram({ type: diagramType, source: parsed, quality, slug })
    },
    onSuccess: async () => {
      await onCreated()
    },
    onError: (caught) => {
      setError(errorMessage(caught, '创建失败'))
      notifyBad(errorMessage(caught, '创建失败'))
    },
  })

  return (
    <Dialog title="新建图表版本" onClose={onClose}>
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="图表类型">
            <Select
              value={diagramType}
              disabled={create.isPending}
              onChange={(event) => setDiagramType(event.target.value as DiagramType)}
            >
              {DIAGRAM_TYPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="校验档位">
            <Select
              value={quality}
              disabled={create.isPending}
              onChange={(event) => setQuality(event.target.value as Quality)}
            >
              {QUALITIES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="JSON 源">
          <textarea
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
            rows={14}
            spellCheck={false}
            disabled={create.isPending}
            placeholder="粘贴 Archify 类型化 JSON 源"
            className="w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs text-paper outline-none placeholder:text-mist/70 focus:border-signal/70"
          />
        </Field>
        {error ? <p className="text-xs text-danger [overflow-wrap:anywhere]">{error}</p> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={create.isPending} onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={create.isPending || !sourceText.trim()} onClick={() => create.mutate()}>
            {create.isPending ? '渲染中…' : '渲染并新增版本'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function SourceDialog({
  siteId,
  versionNo,
  onClose,
  onReuse,
}: {
  siteId: string
  versionNo: number
  onClose: () => void
  onReuse: (source: McpDiagramSource) => void
}) {
  const source = useQuery({
    queryKey: ['mcp-diagram-source', siteId, versionNo],
    queryFn: () => api.mcpDiagramSource(siteId, versionNo),
  })

  const text = source.data ? JSON.stringify(source.data.source, null, 2) : ''

  async function copySource() {
    try {
      await navigator.clipboard.writeText(text)
      notifyOk('已复制 JSON 源')
    } catch {
      notifyBad('复制失败，请手动选择复制')
    }
  }

  return (
    <Dialog title={`图表源 · v${versionNo}`} onClose={onClose}>
      <div className="space-y-3">
        {source.isError ? (
          <p className="text-sm text-danger">{errorMessage(source.error, '加载源失败')}</p>
        ) : null}
        {source.data ? (
          <div className="flex flex-wrap gap-2 text-xs text-mist">
            <span className="rounded-full border border-line px-2 py-0.5">{source.data.type}</span>
            <span className="rounded-full border border-line px-2 py-0.5">{source.data.quality}</span>
          </div>
        ) : null}
        <pre className="max-h-[50vh] max-w-full min-w-0 overflow-auto whitespace-pre-wrap break-all rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
          {source.isLoading ? '加载中…' : text}
        </pre>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            关闭
          </Button>
          <Button type="button" variant="line" disabled={!source.data} onClick={copySource}>
            <Copy size={14} /> 复制源
          </Button>
          <Button type="button" disabled={!source.data} onClick={() => source.data && onReuse(source.data)}>
            基于此源新建版本
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
