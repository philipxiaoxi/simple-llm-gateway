import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Shapes } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

const STATUS_LABELS: Record<string, string> = {
  active: '启用',
  disabled: '已停用',
}

const DIAGRAM_TYPES = ['architecture', 'workflow', 'sequence', 'dataflow', 'lifecycle'] as const
const QUALITIES = ['showcase', 'standard'] as const

const TEMPLATE = `{
  "schema_version": 1,
  "diagram_type": "architecture",
  "meta": { "title": "示例架构", "output": "example.html" },
  "components": [
    { "id": "client", "type": "external", "label": "Client", "pos": [40, 120], "size": [120, 50] },
    { "id": "api", "type": "backend", "label": "API", "pos": [220, 120], "size": [120, 50] }
  ],
  "connections": [
    { "id": "client-to-api", "from": "client", "to": "api", "label": "request" }
  ]
}`

export function McpDiagramsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const keyword = params.get('q') || ''
  const [search, setSearch] = useState(keyword)
  const [open, setOpen] = useState(false)
  const [diagramType, setDiagramType] = useState<(typeof DIAGRAM_TYPES)[number]>('architecture')
  const [quality, setQuality] = useState<(typeof QUALITIES)[number]>('showcase')
  const [sourceText, setSourceText] = useState(TEMPLATE)
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')

  useEffect(() => {
    setSearch(keyword)
  }, [keyword])

  const diagrams = useQuery({
    queryKey: ['mcp-diagrams', keyword],
    queryFn: () => api.mcpDiagrams({ q: keyword }),
    refetchInterval: (query) => {
      const active = query.state.data?.items?.some((item) => item.current_version_id === null)
      return active ? 4000 : 20000
    },
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['mcp-diagrams'] })

  const create = useMutation({
    mutationFn: () => {
      let parsed: unknown
      try {
        parsed = JSON.parse(sourceText)
      } catch {
        throw new Error('图表源不是合法 JSON')
      }
      return api.createMcpDiagram({
        type: diagramType,
        source: parsed,
        quality,
        slug: slug.trim() || undefined,
        name: name.trim() || undefined,
      })
    },
    onSuccess: async (result) => {
      notifyOk('已渲染并创建图表')
      setOpen(false)
      setSlug('')
      setName('')
      setSourceText(TEMPLATE)
      await invalidate()
      navigate(`/mcp-plaza/diagrams/${result.site.id}`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '创建失败')),
  })

  function updateParam(value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set('q', value)
    else next.delete('q')
    setParams(next, { replace: true })
  }

  const items = diagrams.data?.items ?? []

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold">图表生成</h2>
          <p className="mt-1 text-sm text-mist">
            提交 Archify 类型化 JSON 源，渲染为自包含交互式 HTML 并发布为可回滚、可令牌保护的图表站点。
          </p>
        </div>
        <Button type="button" onClick={() => setOpen(true)}>
          <Plus size={15} /> 新建图表
        </Button>
      </div>

      <Card className="p-4">
        <Field label="搜索图表">
          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
              <Input
                className="pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') updateParam(search.trim())
                }}
                placeholder="名称或 slug"
              />
            </div>
            <Button type="button" variant="line" onClick={() => updateParam(search.trim())}>
              搜索
            </Button>
          </div>
        </Field>
      </Card>

      {diagrams.isError ? (
        <div className="text-sm text-danger">{errorMessage(diagrams.error, '加载失败')}</div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.map((site) => (
          <Link
            key={site.id}
            to={`/mcp-plaza/diagrams/${site.id}`}
            className="rounded-xl border border-line bg-panel p-4 transition hover:border-signal/40 hover:bg-panel/80"
          >
            <div className="flex items-center gap-2 text-base font-medium text-paper">
              <Shapes size={17} className="text-signal" />
              <span className="min-w-0 break-all">{site.name || site.slug}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-mist">
              <span className="rounded-full border border-line px-2 py-0.5">/{site.slug}</span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {STATUS_LABELS[site.status] || site.status}
              </span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {site.access_mode === 'token' ? '令牌保护' : '公开'}
              </span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {site.current_version_no ? `v${site.current_version_no}` : '未就绪'}
              </span>
            </div>
            <div className="mt-3 break-all text-xs text-mist [overflow-wrap:anywhere]">{site.preview_url}</div>
            <div className="mt-2 text-xs text-mist">更新 {site.updated_at ? formatTime(site.updated_at) : '—'}</div>
          </Link>
        ))}
      </div>
      {!items.length && !diagrams.isFetching ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
          还没有图表。点击「新建图表」粘贴 JSON 源。
        </div>
      ) : null}

      {open ? (
        <Dialog title="新建图表" onClose={() => (create.isPending ? null : setOpen(false))}>
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="图表类型">
                <Select
                  value={diagramType}
                  disabled={create.isPending}
                  onChange={(event) => setDiagramType(event.target.value as (typeof DIAGRAM_TYPES)[number])}
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
                  onChange={(event) => setQuality(event.target.value as (typeof QUALITIES)[number])}
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
                rows={12}
                spellCheck={false}
                disabled={create.isPending}
                className="w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs text-paper outline-none placeholder:text-mist/70 focus:border-signal/70"
              />
            </Field>
            <Field label="Slug（可选，留空自动生成）">
              <Input
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
                placeholder="my-diagram"
                disabled={create.isPending}
              />
            </Field>
            <Field label="图表名称（可选）">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="系统架构图"
                disabled={create.isPending}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button type="button" disabled={create.isPending} onClick={() => create.mutate()}>
                {create.isPending ? '渲染中…' : '渲染并创建'}
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

export default McpDiagramsPage
