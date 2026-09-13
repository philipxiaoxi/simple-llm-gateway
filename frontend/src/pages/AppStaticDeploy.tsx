import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, FolderUp, Globe, Plus, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type StaticSite } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatBytes, formatTime } from '../lib/utils'

function CreateDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit() {
    setPending(true)
    setError('')
    try {
      await api.createStaticSite({ name, slug, description: description || undefined })
      await queryClient.invalidateQueries({ queryKey: ['static-sites'] })
      notifyOk('站点已创建')
      onClose()
    } catch (caught) {
      setError(errorMessage(caught, '创建失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="新建静态站点" onClose={onClose}>
      <div className="grid gap-3">
        <Field label="名称">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="我的落地页" />
        </Field>
        <Field label="slug（公开路径前缀）">
          <Input
            value={slug}
            onChange={(event) => setSlug(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
            placeholder="landing"
          />
        </Field>
        <div className="text-xs text-mist">访问地址形如 /a/{slug || 'your-slug'}/</div>
        <Field label="备注">
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="可选" />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending || !name.trim() || !slug.trim()} onClick={() => void submit()}>
            创建
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function UploadPanel({ site, onDone }: { site: StaticSite; onDone: () => void }) {
  const zipRef = useRef<HTMLInputElement>(null)
  const dirRef = useRef<HTMLInputElement>(null)
  const [pending, setPending] = useState(false)

  async function uploadZip(file: File | undefined) {
    if (!file) return
    setPending(true)
    try {
      await api.uploadStaticSiteZip(site.id, file)
      notifyOk('部署成功')
      onDone()
    } catch (caught) {
      notifyBad(errorMessage(caught, '部署失败'))
    } finally {
      setPending(false)
    }
  }

  async function uploadFiles(files: FileList | null) {
    const list = Array.from(files || [])
    if (!list.length) return
    setPending(true)
    try {
      await api.uploadStaticSiteFiles(site.id, list)
      notifyOk('部署成功')
      onDone()
    } catch (caught) {
      notifyBad(errorMessage(caught, '部署失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <Button type="button" variant="line" disabled={pending} onClick={() => zipRef.current?.click()}>
        <Upload size={16} /> 上传 zip
      </Button>
      <Button type="button" variant="line" disabled={pending} onClick={() => dirRef.current?.click()}>
        <FolderUp size={16} /> 上传目录/文件
      </Button>
      <input
        ref={zipRef}
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        onChange={(event) => {
          void uploadZip(event.target.files?.[0])
          event.target.value = ''
        }}
      />
      <input
        ref={dirRef}
        type="file"
        className="hidden"
        multiple
        // @ts-expect-error webkitdirectory
        webkitdirectory=""
        directory=""
        onChange={(event) => {
          void uploadFiles(event.target.files)
          event.target.value = ''
        }}
      />
    </div>
  )
}

export function AppStaticDeployPage() {
  const queryClient = useQueryClient()
  const { data: app, isLoading: appLoading } = useQuery({
    queryKey: ['app', 'static-deploy'],
    queryFn: () => api.app('static-deploy'),
  })
  const { data: sites = [], isLoading } = useQuery({
    queryKey: ['static-sites'],
    queryFn: api.staticSites,
    enabled: !!app?.enabled,
  })
  const [creating, setCreating] = useState(false)

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteStaticSite(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['static-sites'] })
      notifyOk('站点已删除')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  if (appLoading) return <Card className="text-sm text-mist">加载中…</Card>

  if (!app?.enabled) {
    return (
      <Card className="grid gap-3">
        <div className="text-lg font-medium text-paper">静态站点部署未启用</div>
        <p className="text-sm text-mist">请先到应用中心启用该应用。</p>
        <Link to="/apps">
          <Button type="button">返回应用中心</Button>
        </Link>
      </Card>
    )
  }

  return (
    <div className="grid gap-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm text-mist">
            <Link to="/apps" className="hover:text-paper">
              应用中心
            </Link>
            <span>/</span>
            <span>静态站点部署</span>
          </div>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-paper">
            <Globe size={22} className="text-signal" /> 静态站点部署
          </h1>
          <p className="mt-1 text-sm text-mist">{"上传 HTML/CSS/JS 或 zip，公开访问路径为 /a/<slug>/。"}</p>
        </div>
        <Button type="button" onClick={() => setCreating(true)}>
          <Plus size={16} /> 新建站点
        </Button>
      </div>

      {isLoading ? <Card className="text-sm text-mist">加载站点列表…</Card> : null}

      {!isLoading && sites.length === 0 ? (
        <Card className="text-sm text-mist">还没有站点。点击「新建站点」开始部署静态前端页面。</Card>
      ) : null}

      <div className="grid gap-3">
        {sites.map((site) => (
          <Card key={site.id} className="grid gap-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-base font-medium text-paper">{site.name}</div>
                  <Badge tone={site.status === 'ready' ? 'ok' : site.status === 'failed' ? 'bad' : 'mist'}>
                    {site.status === 'ready' ? '已部署' : site.status === 'failed' ? '失败' : '待上传'}
                  </Badge>
                </div>
                <div className="mt-1 text-sm text-mist">
                  /a/{site.slug}/ · {site.file_count} 个文件 · {formatBytes(site.total_bytes)}
                </div>
                {site.description ? <div className="mt-1 text-xs text-mist">{site.description}</div> : null}
                {site.error_message ? <div className="mt-1 text-sm text-danger">{site.error_message}</div> : null}
                <div className="mt-1 text-xs text-mist">更新于 {formatTime(site.updated_at)}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                {site.status === 'ready' ? (
                  <a href={site.public_url} target="_blank" rel="noreferrer">
                    <Button type="button" variant="line">
                      <ExternalLink size={16} /> 打开
                    </Button>
                  </a>
                ) : null}
                <Button
                  type="button"
                  variant="danger"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(`确认删除站点 ${site.name}？`)) remove.mutate(site.id)
                  }}
                >
                  <Trash2 size={16} /> 删除
                </Button>
              </div>
            </div>
            <UploadPanel
              site={site}
              onDone={() => {
                void queryClient.invalidateQueries({ queryKey: ['static-sites'] })
              }}
            />
          </Card>
        ))}
      </div>

      {creating ? <CreateDialog onClose={() => setCreating(false)} /> : null}
    </div>
  )
}
