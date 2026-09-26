import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type McpSiteVersion } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatBytes, formatTime } from '../lib/utils'

const VERSION_LABELS: Record<string, string> = {
  unpacking: '解包中',
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

export function McpSiteDetailPage() {
  const { siteId = '' } = useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [entry, setEntry] = useState('')
  const [percent, setPercent] = useState(0)
  const [issuedToken, setIssuedToken] = useState('')

  const site = useQuery({
    queryKey: ['mcp-site', siteId],
    queryFn: () => api.mcpSite(siteId),
    refetchInterval: (query) => {
      const active = query.state.data?.versions?.some((item) => item.status === 'unpacking')
      return active ? 1500 : 15000
    },
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['mcp-site', siteId] })

  const deploy = useMutation({
    mutationFn: () =>
      api.createMcpSiteVersion(siteId, file as File, { entry: entry.trim() || undefined }, setPercent),
    onSuccess: async () => {
      notifyOk('已提交部署，正在解包')
      setOpen(false)
      setFile(null)
      setEntry('')
      setPercent(0)
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '部署失败')),
  })

  const activate = useMutation({
    mutationFn: (versionId: string) => api.activateMcpSiteVersion(siteId, versionId),
    onSuccess: async () => {
      notifyOk('已切换当前版本')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '切换失败')),
  })

  const retry = useMutation({
    mutationFn: (versionId: string) => api.retryMcpSiteVersion(siteId, versionId),
    onSuccess: async () => {
      notifyOk('已重新部署')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重试失败')),
  })

  const removeVersion = useMutation({
    mutationFn: (versionId: string) => api.deleteMcpSiteVersion(siteId, versionId),
    onSuccess: async () => {
      notifyOk('版本已删除')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const update = useMutation({
    mutationFn: (payload: Parameters<typeof api.updateMcpSite>[1]) => api.updateMcpSite(siteId, payload),
    onSuccess: async () => {
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  const generateToken = useMutation({
    mutationFn: () => api.generateMcpSiteToken(siteId),
    onSuccess: async (result) => {
      setIssuedToken(result.token)
      notifyOk('已生成访问令牌')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '生成失败')),
  })

  const removeSite = useMutation({
    mutationFn: () => api.deleteMcpSite(siteId),
    onSuccess: async () => {
      notifyOk('站点已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-sites'] })
      navigate('/mcp-plaza/sites')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  if (site.isError) {
    return <div className="text-sm text-danger">{errorMessage(site.error, '加载失败')}</div>
  }
  if (!site.data) {
    return <div className="text-sm text-mist">加载中…</div>
  }

  const data = site.data
  const versions = data.versions ?? []
  const busy = activate.isPending || retry.isPending || removeVersion.isPending

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza/sites" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回站点列表
      </Link>

      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold">{data.name || data.slug}</h2>
            <div className="mt-2 flex flex-wrap gap-2 text-xs text-mist">
              <span className="rounded-full border border-line px-2 py-0.5">/{data.slug}</span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {data.status === 'active' ? '启用' : '已停用'}
              </span>
              <span className="rounded-full border border-line px-2 py-0.5">
                {data.access_mode === 'token' ? '令牌保护' : '公开'}
              </span>
              {data.current_version_no ? (
                <span className="rounded-full border border-line px-2 py-0.5">当前 v{data.current_version_no}</span>
              ) : null}
            </div>
            <a
              href={data.preview_url}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-block break-all text-sm text-signal hover:underline [overflow-wrap:anywhere]"
            >
              {data.preview_url}
            </a>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button type="button" onClick={() => setOpen(true)}>
              <Upload size={15} /> 部署新版本
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
              disabled={removeSite.isPending}
              onClick={() => {
                if (window.confirm(`删除站点「${data.name || data.slug}」及其全部版本？`)) removeSite.mutate()
              }}
            >
              <Trash2 size={14} /> 删除站点
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
          <Button type="button" variant="line" disabled={generateToken.isPending} onClick={() => generateToken.mutate()}>
            {data.has_token ? '重置令牌' : '生成令牌'}
          </Button>
        </div>
        {issuedToken ? (
          <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
            访问令牌（仅本次展示）：
            <span className="ml-1 break-all font-mono [overflow-wrap:anywhere]">{issuedToken}</span>
            <br />
            访问方式：预览地址后追加 <span className="font-mono">?token=令牌</span>，通过后浏览器会记住。
          </div>
        ) : null}
      </Card>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-paper">版本历史</h3>
        {versions.map((version) => (
          <VersionRow
            key={version.id}
            version={version}
            busy={busy}
            onActivate={() => activate.mutate(version.id)}
            onRetry={() => retry.mutate(version.id)}
            onDelete={() => {
              if (window.confirm(`删除版本 v${version.version_no}？`)) removeVersion.mutate(version.id)
            }}
          />
        ))}
        {!versions.length ? (
          <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center text-sm text-mist">
            还没有版本，点击「部署新版本」上传 zip。
          </div>
        ) : null}
      </div>

      {open ? (
        <Dialog title="部署新版本" onClose={() => (deploy.isPending ? null : setOpen(false))}>
          <div className="space-y-3">
            <Field label="构建产物 (.zip)">
              <input
                type="file"
                accept=".zip,application/zip"
                disabled={deploy.isPending}
                className="block w-full min-w-0 text-sm text-mist file:mr-3 file:rounded-md file:border file:border-line file:bg-panel file:px-3 file:py-2 file:text-sm file:text-paper"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
              />
            </Field>
            <p className="text-xs text-mist">
              支持 Vite/CRA 默认构建产物：以 / 开头的资源路径会自动改写，无需调整 base。
            </p>
            <Field label="入口文件（可选，默认 index.html）">
              <Input value={entry} onChange={(event) => setEntry(event.target.value)} placeholder="index.html" disabled={deploy.isPending} />
            </Field>
            {deploy.isPending ? (
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
              <Button type="button" variant="ghost" disabled={deploy.isPending} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button type="button" disabled={!file || deploy.isPending} onClick={() => deploy.mutate()}>
                上传并部署
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

function VersionRow({
  version,
  busy,
  onActivate,
  onRetry,
  onDelete,
}: {
  version: McpSiteVersion
  busy: boolean
  onActivate: () => void
  onRetry: () => void
  onDelete: () => void
}) {
  const active = version.status === 'unpacking'
  const failed = version.status === 'failed'
  const canActivate = version.status === 'ready' && !version.is_current && !version.purged
  const canDelete = !version.is_current && !version.purged && version.status !== 'unpacking'
  return (
    <Card className="min-w-0 space-y-2 overflow-hidden p-3">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-paper">v{version.version_no}</span>
            <span className={cn('rounded-full border px-2 py-0.5 text-xs', VERSION_TONES[version.status] || 'border-line text-mist')}>
              {VERSION_LABELS[version.status] || version.status}
            </span>
            {version.is_current ? (
              <span className="rounded-full bg-signal/15 px-2 py-0.5 text-xs text-signal">当前版本</span>
            ) : null}
            {version.purged ? <span className="rounded-full bg-white/5 px-2 py-0.5 text-xs text-mist">文件已清理</span> : null}
          </div>
          <div className="mt-1 break-words text-xs text-mist [overflow-wrap:anywhere]">
            {version.source_name} · {version.file_count} 文件 · {formatBytes(version.total_bytes)} · 入口 {version.entry_file}
            <br />
            创建 {version.created_at ? formatTime(version.created_at) : '—'}
            {version.finished_at ? ` · 完成 ${formatTime(version.finished_at)}` : ''}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {canActivate ? (
            <Button type="button" variant="line" disabled={busy} onClick={onActivate}>
              设为当前
            </Button>
          ) : null}
          {failed ? (
            <Button type="button" variant="line" disabled={busy} onClick={onRetry}>
              <RotateCcw size={14} /> 重试
            </Button>
          ) : null}
          {canDelete ? (
            <Button type="button" variant="ghost" disabled={busy} onClick={onDelete}>
              <Trash2 size={14} />
            </Button>
          ) : null}
        </div>
      </div>
      {active || failed ? (
        <div className="min-w-0 rounded-md border border-line bg-ink/60 p-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className={failed ? 'text-danger' : 'text-mist'}>{version.message || '处理中…'}</span>
            <span className="shrink-0 tabular-nums text-mist">{version.percent}%</span>
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-panel-2">
            <div
              className={cn('h-full rounded-full transition-all', failed ? 'bg-danger' : 'bg-signal')}
              style={{ width: `${Math.max(2, Math.min(100, version.percent))}%` }}
            />
          </div>
          {version.error_message ? (
            <div className="mt-2 break-words text-xs text-danger [overflow-wrap:anywhere]">{version.error_message}</div>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

export default McpSiteDetailPage
