import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Globe, Search, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

const STATUS_LABELS: Record<string, string> = {
  active: '启用',
  disabled: '已停用',
}

export function McpSitesPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const keyword = params.get('q') || ''
  const [search, setSearch] = useState(keyword)
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [entry, setEntry] = useState('')
  const [percent, setPercent] = useState(0)

  useEffect(() => {
    setSearch(keyword)
  }, [keyword])

  const sites = useQuery({
    queryKey: ['mcp-sites', keyword],
    queryFn: () => api.mcpSites({ q: keyword }),
    refetchInterval: (query) => {
      const active = query.state.data?.items?.some((item) => item.current_version_id === null)
      return active ? 4000 : 20000
    },
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['mcp-sites'] })

  const create = useMutation({
    mutationFn: () =>
      api.createMcpSite(
        file as File,
        { slug: slug.trim() || undefined, name: name.trim() || undefined, entry: entry.trim() || undefined },
        setPercent,
      ),
    onSuccess: async (result) => {
      notifyOk('已提交部署，正在解包')
      setOpen(false)
      setFile(null)
      setSlug('')
      setName('')
      setEntry('')
      setPercent(0)
      await invalidate()
      navigate(`/mcp-plaza/sites/${result.site.id}`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '部署失败')),
  })

  function updateParam(value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set('q', value)
    else next.delete('q')
    setParams(next, { replace: true })
  }

  const items = sites.data?.items ?? []

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold">站点部署</h2>
          <p className="mt-1 text-sm text-mist">上传前端静态资源 zip（构建产物），生成可访问、可回滚的预览站点。</p>
        </div>
        <Button type="button" onClick={() => setOpen(true)}>
          <Upload size={15} /> 部署新站点
        </Button>
      </div>

      <Card className="p-4">
        <Field label="搜索站点">
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

      {sites.isError ? <div className="text-sm text-danger">{errorMessage(sites.error, '加载失败')}</div> : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.map((site) => (
          <Link
            key={site.id}
            to={`/mcp-plaza/sites/${site.id}`}
            className="rounded-xl border border-line bg-panel p-4 transition hover:border-signal/40 hover:bg-panel/80"
          >
            <div className="flex items-center gap-2 text-base font-medium text-paper">
              <Globe size={17} className="text-signal" />
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
      {!items.length && !sites.isFetching ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
          还没有站点。点击「部署新站点」上传构建产物 zip。
        </div>
      ) : null}

      {open ? (
        <Dialog title="部署新站点" onClose={() => (create.isPending ? null : setOpen(false))}>
          <div className="space-y-3">
            <Field label="构建产物 (.zip)">
              <input
                type="file"
                accept=".zip,application/zip"
                disabled={create.isPending}
                className="block w-full min-w-0 text-sm text-mist file:mr-3 file:rounded-md file:border file:border-line file:bg-panel file:px-3 file:py-2 file:text-sm file:text-paper"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </Field>
            <Field label="Slug（可选，留空自动生成）">
              <Input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="my-demo" disabled={create.isPending} />
            </Field>
            <Field label="站点名称（可选）">
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="演示站" disabled={create.isPending} />
            </Field>
            <Field label="入口文件（可选，默认 index.html）">
              <Input value={entry} onChange={(event) => setEntry(event.target.value)} placeholder="index.html" disabled={create.isPending} />
            </Field>
            {create.isPending ? (
              <div>
                <div className="flex justify-between text-xs text-mist">
                  <span>上传中</span>
                  <span>{percent}%</span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
                  <div className="h-full rounded-full bg-signal transition-all" style={{ width: `${Math.max(2, percent)}%` }} />
                </div>
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button type="button" disabled={!file || create.isPending} onClick={() => create.mutate()}>
                上传并部署
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

export default McpSitesPage
