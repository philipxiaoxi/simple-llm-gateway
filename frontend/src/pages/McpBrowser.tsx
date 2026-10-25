import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, MonitorPlay, RefreshCw, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api, type BrowserSession } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

const STATUS: Record<string, { label: string; tone: 'ok' | 'bad' | 'warn' | 'mist' | 'info' }> = {
  active: { label: '运行中', tone: 'ok' },
  closed: { label: '已关闭', tone: 'mist' },
  expired: { label: '已过期', tone: 'warn' },
  error: { label: '异常', tone: 'bad' },
}

export function McpBrowserPage() {
  const canClose = useCan('mcp_browser:close')
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)

  const status = useQuery({ queryKey: ['browser-status'], queryFn: api.browserStatus })
  const sessions = useQuery({
    queryKey: ['browser-sessions'],
    queryFn: () => api.browserSessions({ limit: 50 }),
    refetchInterval: 5000,
  })
  const items = sessions.data?.items ?? []
  const activeId = selected ?? items.find((item) => item.status === 'active')?.id ?? items[0]?.id ?? null
  const detail = useQuery({
    queryKey: ['browser-session', activeId],
    queryFn: () => api.browserSession(activeId as string),
    enabled: Boolean(activeId),
    refetchInterval: (query) => (query.state.data?.status === 'active' ? 4000 : false),
  })

  const close = useMutation({
    mutationFn: (id: string) => api.closeBrowserSession(id),
    onSuccess: async () => {
      notifyOk('会话已关闭')
      await queryClient.invalidateQueries({ queryKey: ['browser-sessions'] })
      await queryClient.invalidateQueries({ queryKey: ['browser-session'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '关闭失败')),
  })

  const session = detail.data
  const meta = session ? STATUS[session.status] || { label: session.status, tone: 'mist' as const } : null
  const screenshot = session?.screenshot ?? null

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold">在线浏览器</h2>
          <p className="mt-1 text-sm text-mist">AI 通过 MCP 工具控制的浏览器会话，截图可在此实时查看。</p>
        </div>
        {status.data ? (
          <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
            <Badge tone={status.data.enabled ? 'ok' : 'warn'}>{status.data.enabled ? '已启用' : '未启用'}</Badge>
            <span>引擎 {status.data.backend}</span>
            <span>
              会话 {status.data.active_sessions} / {status.data.max_sessions}
            </span>
            <span>单 Key {status.data.max_sessions_per_key}</span>
          </div>
        ) : null}
      </div>

      {sessions.isError ? <div className="text-sm text-danger">{errorMessage(sessions.error, '加载失败')}</div> : null}

      <div className="grid min-w-0 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="min-w-0 space-y-2 p-3">
          <div className="flex items-center justify-between px-1">
            <div className="text-xs uppercase tracking-[0.12em] text-mist">会话（{items.length}）</div>
            <Button
              type="button"
              variant="ghost"
              className="px-2 py-1"
              onClick={() => void sessions.refetch()}
              title="刷新"
            >
              <RefreshCw size={14} className={cn(sessions.isFetching && 'animate-spin')} />
            </Button>
          </div>
          {items.map((item) => (
            <SessionRow
              key={item.id}
              item={item}
              active={item.id === activeId}
              onClick={() => setSelected(item.id)}
            />
          ))}
          {!items.length && !sessions.isFetching ? (
            <div className="rounded-lg border border-dashed border-line px-3 py-10 text-center text-sm text-mist">
              暂无浏览器会话。AI 调用 browser_open 后会出现在这里。
            </div>
          ) : null}
        </Card>

        <Card className="min-w-0 space-y-3 p-4">
          {!session ? (
            <div className="text-sm text-mist">选择左侧会话查看截图。</div>
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 break-words text-sm font-medium text-paper">
                      {session.title || '未命名页面'}
                    </span>
                    {meta ? <Badge tone={meta.tone}>{meta.label}</Badge> : null}
                  </div>
                  <a
                    href={session.current_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-1 block break-all font-mono text-xs text-mist hover:text-paper [overflow-wrap:anywhere]"
                  >
                    {session.current_url || '—'}
                  </a>
                </div>
                <div className="flex shrink-0 gap-2">
                  {session.current_url ? (
                    <a
                      href={session.current_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-h-9 items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-mist hover:text-paper"
                    >
                      <ExternalLink size={14} /> 打开页面
                    </a>
                  ) : null}
                  {canClose && session.status === 'active' ? (
                    <Button
                      type="button"
                      variant="danger"
                      className="px-2 py-1 text-xs"
                      disabled={close.isPending}
                      onClick={() => close.mutate(session.id)}
                    >
                      <XCircle size={14} /> 关闭会话
                    </Button>
                  ) : null}
                </div>
              </div>

              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist">
                <span>视口 {session.viewport.width}×{session.viewport.height}</span>
                <span>引擎 {session.backend}</span>
                <span>最近活动 {session.last_used_at ? formatTime(session.last_used_at) : '—'}</span>
              </div>

              {session.error_message ? (
                <div className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
                  {session.error_message}
                </div>
              ) : null}

              <div className="overflow-hidden rounded-lg border border-line bg-black">
                {screenshot ? (
                  <a href={screenshot.absolute_url} target="_blank" rel="noreferrer" className="block">
                    <img
                      src={screenshot.screenshot_url}
                      alt="当前页面截图"
                      className="max-h-[70vh] w-full object-contain"
                      referrerPolicy="no-referrer"
                    />
                  </a>
                ) : (
                  <div className="flex h-64 flex-col items-center justify-center gap-2 text-mist">
                    <MonitorPlay size={28} />
                    <span className="text-sm">暂无截图</span>
                    <span className="text-xs">AI 调用 browser_screenshot 后可在此查看</span>
                  </div>
                )}
              </div>
              {screenshot ? (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-mist">
                  <span>
                    {screenshot.width}×{screenshot.height} · {Math.max(1, Math.round(screenshot.bytes / 1024))} KB
                  </span>
                  <a href={screenshot.absolute_url} target="_blank" rel="noreferrer" className="text-signal hover:underline">
                    打开原图
                  </a>
                </div>
              ) : null}
            </>
          )}
        </Card>
      </div>
    </div>
  )
}

function SessionRow({
  item,
  active,
  onClick,
}: {
  item: BrowserSession
  active: boolean
  onClick: () => void
}) {
  const meta = STATUS[item.status] || { label: item.status, tone: 'mist' as const }
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full min-w-0 rounded-lg border px-3 py-2 text-left transition',
        active ? 'border-signal/50 bg-signal/10' : 'border-line hover:border-mist/40',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm text-paper">{item.title || '未命名页面'}</span>
        <Badge tone={meta.tone}>{meta.label}</Badge>
      </div>
      <div className="mt-1 truncate font-mono text-xs text-mist">{item.current_url || '—'}</div>
      <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-mist">
        <span className="truncate">{item.mcp_key_name || `Key #${item.mcp_key_id ?? '-'}`}</span>
        <span className="shrink-0">{item.last_used_at ? formatTime(item.last_used_at) : ''}</span>
      </div>
    </button>
  )
}

export default McpBrowserPage
