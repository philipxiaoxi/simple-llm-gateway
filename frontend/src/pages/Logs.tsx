import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Pagination } from '../components/Pagination'
import { Badge, Card, Field, Input, Select } from '../components/ui'
import { api, type LogItem } from '../lib/api'
import { formatTime, formatTokens, LOG_PAGE_SIZE } from '../lib/utils'

const protocolLabel: Record<string, string> = {
  openai_chat: 'OpenAI Chat',
  openai_responses: 'Responses',
  anthropic_messages: 'Anthropic',
}

function accountSourceLabel(source: 'upstream' | 'agent') {
  return source === 'agent' ? '[网关]' : '[上游]'
}

function statusLabel(status: string) {
  if (status === 'success') return '成功'
  if (status === 'error') return '失败'
  return status
}

function formatLogTime(value: string | null | undefined, compact = false) {
  const full = formatTime(value)
  if (full === '—' || !compact) return full
  return full.replace(/^\d{4}-/, '').replace(/:\d{2}$/, '')
}

function accountText(item: LogItem) {
  return item.account_name ? `${accountSourceLabel(item.account_source)} ${item.account_name}` : '—'
}

function EmptyState() {
  return <div className="px-4 py-10 text-center text-sm text-mist">暂无匹配记录</div>
}

export function LogsPage() {
  const { data: accounts = [] } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: keys = [] } = useQuery({ queryKey: ['keys'], queryFn: () => api.keys() })
  const [accountId, setAccountId] = useState('')
  const [keyId, setKeyId] = useState('')
  const [protocol, setProtocol] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = LOG_PAGE_SIZE
  const query = {
    account_id: accountId ? Number(accountId) : undefined,
    api_key_id: keyId ? Number(keyId) : undefined,
    protocol: protocol || undefined,
    status: status || undefined,
    page,
    page_size: pageSize,
  }
  const { data, isPending } = useQuery({ queryKey: ['logs', query], queryFn: () => api.logs(query) })
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  const filteredLogs = useMemo(() => {
    const items = data?.items ?? []
    const keyword = search.trim().toLowerCase()
    if (!keyword) return items
    return items.filter(
      (item) =>
        (item.model ?? '').toLowerCase().includes(keyword) ||
        (item.api_key_name ?? '').toLowerCase().includes(keyword) ||
        (item.account_name ?? '').toLowerCase().includes(keyword),
    )
  }, [data?.items, search])

  function changeFilter(setter: (value: string) => void) {
    return (event: { target: { value: string } }) => {
      setter(event.target.value)
      setPage(1)
    }
  }

  function goToPage(next: number) {
    const bounded = Math.min(Math.max(next, 1), pageCount)
    if (bounded === page) return
    setPage(bounded)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">记录审计</h1>
        <p className="mt-1 text-sm text-mist">按账号、Key、协议和状态筛选请求。</p>
      </div>
      <Card className="grid grid-cols-2 gap-3 lg:flex lg:flex-nowrap lg:items-end">
        <div className="col-span-2 min-w-0 lg:min-w-48 lg:flex-1">
          <Field label="搜索">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索模型 / Key / 账号"
            />
          </Field>
        </div>
        <div className="min-w-0 lg:w-40">
          <Field label="账号">
            <Select value={accountId} onChange={changeFilter(setAccountId)}>
              <option value="">全部账号</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {accountSourceLabel(account.source)} {account.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-36">
          <Field label="Key">
            <Select value={keyId} onChange={changeFilter(setKeyId)}>
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
            <Select value={protocol} onChange={changeFilter(setProtocol)}>
              <option value="">全部协议</option>
              <option value="openai_chat">OpenAI Chat</option>
              <option value="openai_responses">Responses</option>
              <option value="anthropic_messages">Anthropic</option>
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-28">
          <Field label="状态">
            <Select value={status} onChange={changeFilter(setStatus)}>
              <option value="">全部状态</option>
              <option value="success">成功</option>
              <option value="error">失败</option>
            </Select>
          </Field>
        </div>
      </Card>

      <div className="hidden overflow-x-auto rounded-xl border border-line lg:block">
        <table className="w-full min-w-[920px] text-left text-sm">
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="px-3 py-2 font-medium">Key</th>
              <th className="px-3 py-2 font-medium">绑定账号</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">时间</th>
              <th className="px-3 py-2 font-medium">模型</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">协议</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">状态</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">耗时</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">Token</th>
            </tr>
          </thead>
          <tbody>
            {filteredLogs.map((item) => (
              <tr key={item.id} className="border-t border-line hover:bg-white/5">
                <td className="max-w-[180px] px-3 py-2">
                  <Link to={`/logs/${item.id}`} className="block truncate text-paper hover:text-signal" title={item.api_key_name || undefined}>
                    {item.api_key_name || '—'}
                  </Link>
                </td>
                <td className="max-w-[220px] truncate px-3 py-2" title={accountText(item)}>
                  {accountText(item)}
                </td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-mist">
                  {formatLogTime(item.updated_at || item.created_at)}
                </td>
                <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs" title={item.model || undefined}>
                  {item.model || '—'}
                </td>
                <td className="whitespace-nowrap px-3 py-2">{protocolLabel[item.protocol] || item.protocol}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <Badge tone={item.status === 'success' ? 'ok' : 'bad'}>{statusLabel(item.status)}</Badge>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums">{item.latency_ms}ms</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums">{formatTokens(item)}</td>
              </tr>
            ))}
            {!isPending && filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={8}>
                  <EmptyState />
                </td>
              </tr>
            ) : null}
            {isPending && filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-sm text-mist">
                  加载中…
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:hidden">
        {filteredLogs.map((item) => (
          <Link key={item.id} to={`/logs/${item.id}`} className="block min-h-11">
            <Card className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-medium" title={item.api_key_name || undefined}>
                    {item.api_key_name || '—'}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-mist" title={accountText(item)}>
                    {accountText(item)}
                  </div>
                </div>
                <Badge tone={item.status === 'success' ? 'ok' : 'bad'}>{statusLabel(item.status)}</Badge>
              </div>
              <div className="truncate font-mono text-xs" title={item.model || undefined}>
                {item.model || '—'}
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">协议</div>
                  <div className="mt-1 truncate text-xs">{protocolLabel[item.protocol] || item.protocol}</div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">耗时</div>
                  <div className="mt-1 font-mono text-xs tabular-nums">{item.latency_ms}ms</div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">Token</div>
                  <div className="mt-1 font-mono text-xs tabular-nums">{formatTokens(item)}</div>
                </div>
              </div>
              <div className="text-xs text-mist">{formatLogTime(item.updated_at || item.created_at, true)}</div>
            </Card>
          </Link>
        ))}
        {isPending && filteredLogs.length === 0 ? <div className="px-4 py-10 text-center text-sm text-mist">加载中…</div> : null}
        {!isPending && filteredLogs.length === 0 ? (
          <Card>
            <EmptyState />
          </Card>
        ) : null}
      </div>

      <Pagination page={page} pageCount={pageCount} total={total} onPage={goToPage} />
    </div>
  )
}
