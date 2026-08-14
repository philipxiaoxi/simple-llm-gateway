import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card } from '../components/ui'
import { api } from '../lib/api'
import { formatTime } from '../lib/utils'

const protocolLabel: Record<string, string> = {
  openai_chat: 'OpenAI Chat',
  openai_responses: 'Responses',
  anthropic_messages: 'Anthropic',
}

export function LogsPage() {
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const { data: keys = [] } = useQuery({ queryKey: ['keys'], queryFn: api.keys })
  const [accountId, setAccountId] = useState('')
  const [keyId, setKeyId] = useState('')
  const [protocol, setProtocol] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20
  const query = {
    account_id: accountId ? Number(accountId) : undefined,
    api_key_id: keyId ? Number(keyId) : undefined,
    protocol: protocol || undefined,
    status: status || undefined,
    page,
    page_size: pageSize,
  }
  const { data } = useQuery({ queryKey: ['logs', query], queryFn: () => api.logs(query) })
  const logs = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  function changeFilter(setter: (value: string) => void) {
    return (event: { target: { value: string } }) => {
      setter(event.target.value)
      setPage(1)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">记录审计</h1>
        <p className="mt-1 text-sm text-mist">按账号、Key、协议和状态筛选请求。</p>
      </div>
      <Card className="grid gap-3 md:grid-cols-4">
        <select className="rounded-md border border-line bg-ink px-3 py-2 text-sm" value={accountId} onChange={changeFilter(setAccountId)}>
          <option value="">全部账号</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
            </option>
          ))}
        </select>
        <select className="rounded-md border border-line bg-ink px-3 py-2 text-sm" value={keyId} onChange={changeFilter(setKeyId)}>
          <option value="">全部 Key</option>
          {keys.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <select className="rounded-md border border-line bg-ink px-3 py-2 text-sm" value={protocol} onChange={changeFilter(setProtocol)}>
          <option value="">全部协议</option>
          <option value="openai_chat">OpenAI Chat</option>
          <option value="openai_responses">Responses</option>
          <option value="anthropic_messages">Anthropic</option>
        </select>
        <select className="rounded-md border border-line bg-ink px-3 py-2 text-sm" value={status} onChange={changeFilter(setStatus)}>
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="error">失败</option>
        </select>
      </Card>
      <div className="hidden overflow-hidden rounded-xl border border-line md:block">
        <table className="w-full text-left text-sm">
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="px-3 py-2 font-medium">时间</th>
              <th className="px-3 py-2 font-medium">模型</th>
              <th className="px-3 py-2 font-medium">协议</th>
              <th className="px-3 py-2 font-medium">状态</th>
              <th className="px-3 py-2 font-medium">耗时</th>
              <th className="px-3 py-2 font-medium">Token</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((item) => (
              <tr key={item.id} className="border-t border-line hover:bg-white/5">
                <td className="px-3 py-2">
                  <Link to={`/logs/${item.id}`} className="text-paper hover:text-signal">
                    {formatTime(item.created_at)}
                  </Link>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{item.model}</td>
                <td className="px-3 py-2">{protocolLabel[item.protocol] || item.protocol}</td>
                <td className="px-3 py-2">
                  <Badge tone={item.status === 'success' ? 'ok' : 'bad'}>{item.status}</Badge>
                </td>
                <td className="px-3 py-2">{item.latency_ms}ms</td>
                <td className="px-3 py-2">{item.total_tokens ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:hidden">
        {logs.map((item) => (
          <Link key={item.id} to={`/logs/${item.id}`}>
            <Card>
              <div className="flex items-center justify-between">
                <div className="font-mono text-xs">{item.model}</div>
                <Badge tone={item.status === 'success' ? 'ok' : 'bad'}>{item.status}</Badge>
              </div>
              <div className="mt-2 text-sm">{formatTime(item.created_at)}</div>
              <div className="mt-1 text-xs text-mist">
                {protocolLabel[item.protocol]} · {item.latency_ms}ms
              </div>
            </Card>
          </Link>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-mist">
          共 {total} 条 · 第 {page} / {pageCount} 页
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="line" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>
            上一页
          </Button>
          <Button
            type="button"
            variant="line"
            disabled={page >= pageCount}
            onClick={() => setPage((current) => current + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </div>
  )
}
