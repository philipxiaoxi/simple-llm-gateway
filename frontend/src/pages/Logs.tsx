import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pagination } from '../components/Pagination'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { api, type LogItem } from '../lib/api'
import { useCan } from '../lib/session'
import {
  accountText,
  formatClock,
  groupByDate,
  protocolLabel,
  sessionTitle,
  statusMeta,
  type Tone,
} from '../lib/logView'
import { LOG_PAGE_SIZE, cn, formatTime, formatTokens } from '../lib/utils'

type StatusFilter = '' | 'success' | 'error'

const DOT: Record<Tone, string> = {
  ok: 'bg-signal',
  bad: 'bg-danger',
  warn: 'bg-warn',
  info: 'bg-info',
  mist: 'bg-mist',
}

const TEXT: Record<Tone, string> = {
  ok: 'text-signal',
  bad: 'text-danger',
  warn: 'text-warn',
  info: 'text-info',
  mist: 'text-mist',
}

function StatusIndicator({ status }: { status: string }) {
  const meta = statusMeta(status)
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium">
      <span className={cn('h-2 w-2 rounded-full', DOT[meta.tone])} aria-hidden="true" />
      <span className={TEXT[meta.tone]}>{meta.label}</span>
    </span>
  )
}

function SessionRow({ item }: { item: LogItem }) {
  const canDetail = useCan('logs:detail')
  const turns = item.turn_count ?? 0
  const merged = turns > 1
  const callLatency = item.last_latency_ms ?? item.latency_ms
  const title = sessionTitle(item)
  const time = formatClock(item.updated_at || item.created_at)
  const fullTime = formatTime(item.updated_at || item.created_at)
  const body = (
    <>
      <div className="flex items-center justify-between gap-3 lg:block lg:pt-1">
        <StatusIndicator status={item.status} />
        <time className="font-mono text-xs text-mist lg:hidden" title={fullTime}>
          {time}
        </time>
      </div>
      <div className="mt-2 min-w-0 lg:mt-0">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-sm text-paper" title={title}>
            {title}
          </span>
          <Badge tone="mist" className="hidden shrink-0 lg:inline-flex">
            {protocolLabel(item.protocol)}
          </Badge>
          <span className="shrink-0 text-xs text-mist lg:hidden">{protocolLabel(item.protocol)}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-mist">
          <span className="max-w-[14rem] truncate" title={item.api_key_name || undefined}>
            {item.api_key_name || '—'}
          </span>
          <span className="text-mist/40">·</span>
          <span className="max-w-[14rem] truncate" title={accountText(item)}>
            {accountText(item)}
          </span>
          <span className="text-mist/40">·</span>
          <span className="tabular-nums">{merged ? `会话 · ${turns} 次调用` : `${turns} 轮`}</span>
          <span className="text-mist/40">·</span>
          <span
            className="tabular-nums"
            title={merged ? '会话累计 Token' : undefined}
          >
            {formatTokens(item)}
          </span>
          <span className="text-mist/40">·</span>
          <span
            className="tabular-nums"
            title={merged ? `最近一次调用，会话累计 ${item.latency_ms}ms` : undefined}
          >
            {callLatency}ms
          </span>
          {item.client_ip ? (
            <>
              <span className="text-mist/40">·</span>
              <span className="font-mono" title={item.ip_location || undefined}>
                {item.client_ip}
              </span>
            </>
          ) : null}
        </div>
      </div>
      <time className="hidden text-right font-mono text-xs text-mist lg:block" title={fullTime}>
        {time}
      </time>
    </>
  )
  const className =
    'block px-4 py-3 lg:grid lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-start lg:gap-x-5'
  if (!canDetail) {
    return <div className={cn(className, 'cursor-default')}>{body}</div>
  }
  return (
    <Link to={`/logs/${item.id}`} className={cn(className, 'transition hover:bg-white/[0.03]')}>
      {body}
    </Link>
  )
}

export function LogsPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: keys = [] } = useQuery({ queryKey: ['keys', 'last_used'], queryFn: () => api.keys('last_used') })
  const [accountId, setAccountId] = useState('')
  const [keyId, setKeyId] = useState('')
  const [protocol, setProtocol] = useState('')
  const [status, setStatus] = useState<StatusFilter>('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = LOG_PAGE_SIZE
  const query = useMemo(
    () => ({
      account_id: accountId ? Number(accountId) : undefined,
      api_key_id: keyId ? Number(keyId) : undefined,
      protocol: protocol || undefined,
      status: status || undefined,
      q: debouncedSearch || undefined,
      page,
      page_size: pageSize,
    }),
    [accountId, keyId, protocol, status, debouncedSearch, page, pageSize],
  )
  const { data, isPending, isFetching } = useQuery({
    queryKey: ['logs', query],
    queryFn: () => api.logs(query),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  })
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const items = useMemo(() => data?.items ?? [], [data])
  const groups = useMemo(() => groupByDate(items), [items])

  const [newCount, setNewCount] = useState(0)
  const maxIdRef = useRef<number | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const [atTop, setAtTop] = useState(true)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    maxIdRef.current = null
    setNewCount(0)
  }, [accountId, keyId, protocol, status, debouncedSearch])

  useEffect(() => {
    if (page !== 1 || items.length === 0) return
    const topId = items[0].id
    const previous = maxIdRef.current
    if (previous == null) {
      maxIdRef.current = topId
      return
    }
    if (topId > previous) {
      const added = items.filter((item) => item.id > previous).length
      if (!atTop) setNewCount((count) => count + added)
      maxIdRef.current = topId
    }
  }, [items, page, atTop])

  useEffect(() => {
    const node = sentinelRef.current
    if (!node) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) setAtTop(entry.isIntersecting)
      },
      { rootMargin: '0px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  function scrollToTop() {
    const main = document.querySelector('main')
    if (main instanceof HTMLElement) main.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
    window.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
    setNewCount(0)
  }

  function changeFilter<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value)
      setPage(1)
    }
  }

  function goToPage(next: number) {
    const bounded = Math.min(Math.max(next, 1), pageCount)
    if (bounded === page) return
    setPage(bounded)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const hasFilter = Boolean(debouncedSearch || accountId || keyId || protocol || status)
  const showEmpty = !isPending && items.length === 0

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold">请求记录</h1>
            <span className="inline-flex items-center gap-1 text-xs text-mist">
              <span
                className={cn('h-2 w-2 rounded-full', isFetching ? 'animate-pulse bg-warn' : 'bg-signal')}
                aria-hidden="true"
              />
              实时同步
            </span>
          </div>
          <p className="mt-1 text-sm text-mist">按会话聚合的模型调用记录，可查看对话内容与执行链路。</p>
        </div>
        <Button
          variant="line"
          onClick={() => {
            void queryClient.invalidateQueries({ queryKey: ['logs'] })
            setNewCount(0)
          }}
        >
          <RefreshCw size={14} className={cn(isFetching && 'animate-spin')} />
          刷新
        </Button>
      </div>

      <Card className="grid grid-cols-2 gap-3 lg:flex lg:flex-nowrap lg:items-end">
        <div className="col-span-2 min-w-0 lg:min-w-48 lg:flex-1">
          <Field label="搜索">
            <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="模型 / Key / 账号 / 会话" />
          </Field>
        </div>
        <div className="min-w-0 lg:w-40">
          <Field label="账号">
            <Select value={accountId} onChange={(event) => changeFilter(setAccountId)(event.target.value)}>
              <option value="">全部账号</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.source === 'agent' ? '[网关]' : '[上游]'} {account.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-36">
          <Field label="Key">
            <Select value={keyId} onChange={(event) => changeFilter(setKeyId)(event.target.value)}>
              <option value="">全部 Key</option>
              {keys.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-36">
          <Field label="协议">
            <Select value={protocol} onChange={(event) => changeFilter(setProtocol)(event.target.value)}>
              <option value="">全部协议</option>
              <option value="openai_chat">OpenAI Chat</option>
              <option value="openai_responses">Responses</option>
              <option value="anthropic_messages">Anthropic</option>
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-28">
          <Field label="状态">
            <Select value={status} onChange={(event) => changeFilter<StatusFilter>(setStatus)(event.target.value as StatusFilter)}>
              <option value="">全部状态</option>
              <option value="success">完成</option>
              <option value="error">失败</option>
            </Select>
          </Field>
        </div>
      </Card>

      <div ref={sentinelRef} className="h-px" aria-hidden="true" />

      <div className="space-y-4">
        {groups.map((group) => (
          <section key={group.key}>
            <div className="mb-1.5 flex items-center gap-2 px-1 text-xs font-medium text-mist">
              <span>{group.label}</span>
              <span className="tabular-nums text-mist-strong">{group.items.length} 条</span>
            </div>
            <div className="divide-y divide-line/60 overflow-hidden rounded-xl border border-line/70 bg-panel/40">
              {group.items.map((item) => (
                <SessionRow key={item.id} item={item} />
              ))}
            </div>
          </section>
        ))}
        {isPending && items.length === 0 ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="h-16 animate-pulse rounded-xl bg-white/[0.04]" />
            ))}
          </div>
        ) : null}
        {showEmpty ? (
          <Card>
            <div className="px-4 py-12 text-center text-sm text-mist">
              {hasFilter ? (
                <div className="space-y-3">
                  <div>没有匹配的会话</div>
                  <Button
                    variant="line"
                    onClick={() => {
                      setSearch('')
                      setDebouncedSearch('')
                      setAccountId('')
                      setKeyId('')
                      setProtocol('')
                      setStatus('')
                      setPage(1)
                    }}
                  >
                    清除筛选
                  </Button>
                </div>
              ) : (
                '还没有请求记录，客户端发起调用后会出现在这里。'
              )}
            </div>
          </Card>
        ) : null}
      </div>

      {newCount > 0 && !atTop ? (
        <button
          type="button"
          onClick={scrollToTop}
          className="fixed bottom-20 left-1/2 z-30 -translate-x-1/2 rounded-full bg-signal px-4 py-2 text-sm font-medium text-ink shadow-lg lg:bottom-8"
        >
          <span className="inline-flex items-center gap-1">
            <ArrowUp size={14} />
            {newCount} 条新记录
          </span>
        </button>
      ) : null}

      <Pagination page={page} pageCount={pageCount} total={total} unit="条记录" onPage={goToPage} />

      <div className="text-right text-xs text-mist">当前页 {items.length} 条记录 · 实时同步每 5 秒</div>
    </div>
  )
}
