import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Download, Link2, Search, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Switch } from '../components/ui'
import { api, type DouyinJob } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

const STATUS: Record<string, { label: string; tone: 'ok' | 'bad' | 'warn' | 'mist' | 'info' }> = {
  queued: { label: '排队中', tone: 'mist' },
  downloading: { label: '转存中', tone: 'info' },
  succeeded: { label: '已完成', tone: 'ok' },
  partial: { label: '部分完成', tone: 'warn' },
  failed: { label: '失败', tone: 'bad' },
}

const TERMINAL = new Set(['succeeded', 'partial', 'failed'])

export function McpDouyinPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const keyword = params.get('q') || ''
  const [search, setSearch] = useState(keyword)
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [rehost, setRehost] = useState(true)

  const jobs = useQuery({
    queryKey: ['mcp-douyin', keyword],
    queryFn: () => api.mcpDouyinJobs({ q: keyword }),
    refetchInterval: (query) => {
      const active = query.state.data?.items?.some((item) => !TERMINAL.has(item.status))
      return active ? 1500 : 20000
    },
  })

  const provider = useQuery({ queryKey: ['mcp-douyin-provider'], queryFn: () => api.mcpDouyinProvider() })
  const [providerBase, setProviderBase] = useState('')
  const [providerKey, setProviderKey] = useState('')

  useEffect(() => {
    if (provider.data) setProviderBase(provider.data.base_url || '')
  }, [provider.data])

  const saveProvider = useMutation({
    mutationFn: () =>
      api.saveMcpDouyinProvider({
        base_url: providerBase.trim(),
        ...(providerKey.trim() ? { api_key: providerKey.trim() } : {}),
      }),
    onSuccess: async () => {
      notifyOk('TikHub 配置已保存')
      setProviderKey('')
      await queryClient.invalidateQueries({ queryKey: ['mcp-douyin-provider'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const clearProvider = useMutation({
    mutationFn: () => api.clearMcpDouyinProvider(),
    onSuccess: async () => {
      notifyOk('TikHub 配置已清除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-douyin-provider'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '清除失败')),
  })

  const create = useMutation({
    mutationFn: () => api.createMcpDouyinJob({ share_text: text.trim(), rehost }),
    onSuccess: async (job) => {
      notifyOk('已提交解析，正在转存')
      setOpen(false)
      setText('')
      setRehost(true)
      await queryClient.invalidateQueries({ queryKey: ['mcp-douyin'] })
      navigate(`/mcp-plaza/douyin/${job.id}`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '解析失败')),
  })

  function updateParam(value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set('q', value)
    else next.delete('q')
    setParams(next, { replace: true })
  }

  const items = jobs.data?.items ?? []

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold">抖音视频下载</h2>
          <p className="mt-1 text-sm text-mist">粘贴分享文案或直链，转存视频/图集并生成稳定下载地址。</p>
        </div>
        <Button type="button" onClick={() => setOpen(true)}>
          <Link2 size={15} /> 解析链接
        </Button>
      </div>

      <Card className="p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-paper">
          <Cloud size={16} className="text-signal" /> TikHub 解析 API
        </div>
        <p className="mt-1 text-xs text-mist">
          抖音作品统一通过 TikHub 托管 API 解析，平台侧反爬由 TikHub 处理。填入 Base URL 与 API Key；Key 加密存储、不回显。
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-mist">
          {provider.data?.configured ? <Badge tone="ok">已配置</Badge> : <Badge tone="warn">未配置</Badge>}
          {provider.data?.source ? (
            <span>
              来源：
              {provider.data.source === 'page' ? '管理页' : provider.data.source === 'env' ? '环境变量' : provider.data.source}
            </span>
          ) : null}
          {provider.data?.updated_at ? <span>更新：{formatTime(provider.data.updated_at)}</span> : null}
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          <Field label="Base URL">
            <Input
              value={providerBase}
              onChange={(event) => setProviderBase(event.target.value)}
              placeholder="https://api.tikhub.io"
              disabled={saveProvider.isPending}
            />
          </Field>
          <Field label="API Key（留空则不修改）">
            <Input
              type="password"
              value={providerKey}
              onChange={(event) => setProviderKey(event.target.value)}
              placeholder={provider.data?.has_key ? '已配置' : 'tk_...'}
              disabled={saveProvider.isPending}
            />
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={saveProvider.isPending || !providerBase.trim()}
            onClick={() => saveProvider.mutate()}
          >
            {saveProvider.isPending ? '保存中…' : '保存'}
          </Button>
          {provider.data?.source === 'page' ? (
            <Button
              type="button"
              variant="danger"
              disabled={clearProvider.isPending}
              onClick={() => {
                if (window.confirm('清除 TikHub 配置？')) clearProvider.mutate()
              }}
            >
              <Trash2 size={14} /> 清除
            </Button>
          ) : null}
        </div>
      </Card>

      <Card className="p-4">
        <Field label="搜索任务">
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
                placeholder="标题或作者"
              />
            </div>
            <Button type="button" variant="line" onClick={() => updateParam(search.trim())}>
              搜索
            </Button>
          </div>
        </Field>
      </Card>

      {jobs.isError ? <div className="text-sm text-danger">{errorMessage(jobs.error, '加载失败')}</div> : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.map((job) => (
          <JobCard key={job.id} job={job} />
        ))}
      </div>
      {!items.length && !jobs.isFetching ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
          还没有下载任务。点击「解析链接」粘贴抖音分享文案。
        </div>
      ) : null}

      {open ? (
        <Dialog title="解析抖音链接" onClose={() => (create.isPending ? null : setOpen(false))}>
          <div className="space-y-3">
            <Field label="分享文案或直链">
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                disabled={create.isPending}
                rows={4}
                placeholder="粘贴抖音分享文案，或直接粘贴 https://v.douyin.com/xxxx/ 链接"
                className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none focus:border-signal/70"
              />
            </Field>
            <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
              <div>
                <div className="text-sm text-paper">转存到平台</div>
                <div className="text-xs text-mist">关闭则只解析元数据与原始直链，不占用平台磁盘。</div>
              </div>
              <Switch checked={rehost} onCheckedChange={setRehost} />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button type="button" disabled={!text.trim() || create.isPending} onClick={() => create.mutate()}>
                解析
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

function JobCard({ job }: { job: DouyinJob }) {
  const status = STATUS[job.status] || { label: job.status, tone: 'mist' as const }
  return (
    <Link
      to={`/mcp-plaza/douyin/${job.id}`}
      className="flex gap-3 rounded-xl border border-line bg-panel p-3 transition hover:border-signal/40 hover:bg-panel/80"
    >
      <div className="h-20 w-16 shrink-0 overflow-hidden rounded-md border border-line bg-ink">
        {job.cover_url ? (
          <img src={job.cover_url} alt="" className="h-full w-full object-cover" referrerPolicy="no-referrer" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-mist">
            <Download size={18} />
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-sm font-medium text-paper">{job.title || job.aweme_id || '未命名作品'}</div>
        <div className="mt-1 truncate text-xs text-mist">{job.author_name || '—'}</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge tone={status.tone}>{status.label}</Badge>
          <Badge tone="mist">{job.kind === 'gallery' ? '图集' : '视频'}</Badge>
          <Badge tone="mist">{job.media_count} 项</Badge>
        </div>
        {!TERMINAL.has(job.status) ? (
          <div className="mt-2">
            <div className="h-1 w-full overflow-hidden rounded-full bg-panel-2">
              <div
                className="h-full rounded-full bg-signal transition-all"
                style={{ width: `${Math.min(100, Math.max(2, job.percent))}%` }}
              />
            </div>
            <div className="mt-1 text-xs text-mist">
              {job.message || '处理中'} · {job.percent}%
            </div>
          </div>
        ) : (
          <div className="mt-2 text-xs text-mist">{job.created_at ? formatTime(job.created_at) : '—'}</div>
        )}
      </div>
    </Link>
  )
}

export default McpDouyinPage
