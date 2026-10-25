import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Inbox, RefreshCw, Search } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Input } from '../components/ui'
import { api, type InfoReportItem } from '../lib/api'
import { cn, errorMessage, formatTime } from '../lib/utils'

const LIMIT = 20

const KIND_LABEL: Record<string, string> = {
  text: '纯文字',
  image: '图片',
  video: '视频',
  mixed: '图文/视频',
}

function statusBadge(item: InfoReportItem) {
  if (item.is_hidden) return <Badge tone="bad">已隐藏</Badge>
  if (item.is_featured) return <Badge tone="ok">精选</Badge>
  if (item.ai_status === 'pending' || item.ai_status === 'processing') return <Badge tone="info">待判定</Badge>
  if (item.ai_status === 'failed') return <Badge tone="warn">判定失败</Badge>
  return <Badge tone="mist">普通</Badge>
}

export function McpInfoReportsPage() {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [offset, setOffset] = useState(0)

  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['info-reports', keyword, offset],
    queryFn: () => api.infoReports({ limit: LIMIT, offset, q: keyword }),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const page = Math.floor(offset / LIMIT) + 1
  const pages = Math.max(1, Math.ceil(total / LIMIT))

  return (
    <div className="min-w-0 space-y-4 overflow-x-hidden">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>

      <div>
        <h2 className="text-xl font-semibold">资讯上报历史</h2>
        <p className="mt-1 text-sm text-mist">
          Agent 经 MCP / REST 上送过的资讯，按时间倒序，便于回溯上报了哪些内容。
        </p>
      </div>

      <Card className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-end">
        <div className="relative">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
          <Input
            className="pl-9"
            value={search}
            placeholder="搜索正文关键词"
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                setOffset(0)
                setKeyword(search.trim())
              }
            }}
          />
        </div>
        <Button
          type="button"
          variant="line"
          onClick={() => {
            setOffset(0)
            setKeyword(search.trim())
          }}
        >
          搜索
        </Button>
        <Button type="button" variant="ghost" onClick={() => void refetch()} title="刷新">
          <RefreshCw size={16} className={cn(isFetching && 'animate-spin')} />
        </Button>
      </Card>

      {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载上报历史失败')}</div> : null}

      <div className="space-y-2">
        {items.map((item) => (
          <Card key={item.id} className="min-w-0 space-y-2 p-3">
            <div className="flex min-w-0 items-start justify-between gap-3">
              <Link
                to={`/info/${item.id}`}
                title={item.title || item.excerpt}
                className="min-w-0 break-words text-sm font-medium text-paper hover:text-signal [overflow-wrap:anywhere]"
              >
                {item.title || '（无标题）'}
              </Link>
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                {statusBadge(item)}
                <Badge tone="mist">{KIND_LABEL[item.kind] || item.kind}</Badge>
              </div>
            </div>
            {item.excerpt ? (
              <p className="line-clamp-2 break-words text-xs text-mist [overflow-wrap:anywhere]">{item.excerpt}</p>
            ) : null}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-mist">
              <span>上报者：{item.author_name || '未知'}</span>
              <span>媒体：{item.media_count}</span>
              <span>{formatTime(item.created_at)}</span>
              {item.permalink ? (
                <a
                  href={item.permalink}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-signal hover:underline"
                >
                  原始链接 <ExternalLink size={12} />
                </a>
              ) : null}
            </div>
          </Card>
        ))}
      </div>

      {!items.length && !isFetching ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-16 text-center text-sm text-mist">
          <Inbox className="mx-auto mb-2 text-mist" size={26} />
          {keyword ? '没有匹配的上报记录' : '还没有上报记录'}
        </div>
      ) : null}

      {total > LIMIT ? (
        <div className="flex items-center justify-between gap-2 text-xs text-mist">
          <span>
            共 {total} 条 · 第 {page} / {pages} 页
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="line"
              disabled={offset === 0 || isFetching}
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            >
              上一页
            </Button>
            <Button
              type="button"
              variant="line"
              disabled={offset + LIMIT >= total || isFetching}
              onClick={() => setOffset(offset + LIMIT)}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default McpInfoReportsPage
