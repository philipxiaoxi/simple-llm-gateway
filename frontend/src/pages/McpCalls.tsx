import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { errorMessage, formatTime } from '../lib/utils'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'

const PAGE_SIZES = [25, 50, 100]

export function McpCallsPage() {
  const [capabilityId, setCapabilityId] = useState('')
  const [keyId, setKeyId] = useState('')
  const [result, setResult] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [limit, setLimit] = useState(50)
  const [page, setPage] = useState(0)

  const catalog = useQuery({ queryKey: ['mcp-catalog'], queryFn: api.mcpCatalog })
  const keys = useQuery({ queryKey: ['mcp-keys'], queryFn: api.mcpKeys })

  const offset = page * limit
  const calls = useQuery({
    queryKey: ['mcp-calls', { limit, offset, capabilityId, keyId, result, keyword }],
    queryFn: () =>
      api.mcpCalls({
        limit,
        offset,
        capability_id: capabilityId || undefined,
        mcp_key_id: keyId ? Number(keyId) : undefined,
        success: result === '' ? undefined : result === 'ok',
        q: keyword || undefined,
      }),
    refetchInterval: 15_000,
  })

  const data = calls.data
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / limit))
  const hasFilter = Boolean(capabilityId || keyId || result || keyword)

  function resetToFirstPage() {
    setPage(0)
  }

  function clearFilters() {
    setCapabilityId('')
    setKeyId('')
    setResult('')
    setKeywordInput('')
    setKeyword('')
    resetToFirstPage()
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-mist">授权通过后的能力调用都会记录，成功与失败均可查。当前共 {total} 条。</p>

      <Card className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="能力">
          <Select
            value={capabilityId}
            onChange={(event) => {
              setCapabilityId(event.target.value)
              resetToFirstPage()
            }}
          >
            <option value="">全部能力</option>
            {(catalog.data?.items ?? []).map((item) => (
              <option key={item.capability_id} value={item.capability_id}>
                {item.name}（{item.capability_id}）
              </option>
            ))}
          </Select>
        </Field>
        <Field label="MCP Key">
          <Select
            value={keyId}
            onChange={(event) => {
              setKeyId(event.target.value)
              resetToFirstPage()
            }}
          >
            <option value="">全部 Key</option>
            {(keys.data ?? []).map((item) => (
              <option key={item.id} value={String(item.id)}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="结果">
          <Select
            value={result}
            onChange={(event) => {
              setResult(event.target.value)
              resetToFirstPage()
            }}
          >
            <option value="">全部</option>
            <option value="ok">成功</option>
            <option value="fail">失败</option>
          </Select>
        </Field>
        <Field label="搜索（操作 / Key / 错误）">
          <div className="flex gap-2">
            <Input
              value={keywordInput}
              onChange={(event) => setKeywordInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  setKeyword(keywordInput.trim())
                  resetToFirstPage()
                }
              }}
              placeholder="search / 错误关键词"
            />
            <Button
              type="button"
              variant="line"
              onClick={() => {
                setKeyword(keywordInput.trim())
                resetToFirstPage()
              }}
            >
              查询
            </Button>
          </div>
        </Field>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm text-mist">
          第 {page + 1} / {pageCount} 页 · 共 {total} 条
          {hasFilter ? '（已筛选）' : ''}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {hasFilter ? (
            <Button type="button" variant="ghost" onClick={clearFilters}>
              重置
            </Button>
          ) : null}
          <Select
            className="w-auto"
            value={String(limit)}
            onChange={(event) => {
              setLimit(Number(event.target.value))
              resetToFirstPage()
            }}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={String(size)}>
                每页 {size} 条
              </option>
            ))}
          </Select>
          <Button type="button" variant="line" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
            上一页
          </Button>
          <Button
            type="button"
            variant="line"
            disabled={page + 1 >= pageCount}
            onClick={() => setPage((value) => value + 1)}
          >
            下一页
          </Button>
        </div>
      </div>

      {calls.isError ? <div className="text-sm text-danger">{errorMessage(calls.error, '加载失败')}</div> : null}
      {calls.isFetching && !items.length ? <div className="text-sm text-mist">加载中…</div> : null}

      <div className="overflow-x-auto rounded-xl border border-line">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-panel text-mist">
            <tr>
              <th className="px-3 py-2">时间</th>
              <th className="px-3 py-2">Key</th>
              <th className="px-3 py-2">能力</th>
              <th className="px-3 py-2">操作</th>
              <th className="px-3 py-2">结果</th>
              <th className="px-3 py-2">耗时</th>
              <th className="px-3 py-2">错误</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t border-line align-top">
                <td className="px-3 py-2 whitespace-nowrap">{formatTime(item.created_at)}</td>
                <td className="px-3 py-2">
                  <div className="text-paper">{item.mcp_key_name || '-'}</div>
                  <div className="font-mono text-xs text-mist">{item.mcp_key_prefix || '-'}</div>
                </td>
                <td className="px-3 py-2">{item.capability_id}</td>
                <td className="px-3 py-2 font-mono text-xs">{item.operation}</td>
                <td className="px-3 py-2">
                  <Badge tone={item.success ? 'ok' : 'bad'}>{item.success ? '成功' : '失败'}</Badge>
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{item.latency_ms} ms</td>
                <td className="px-3 py-2 max-w-lg whitespace-pre-wrap break-words text-mist">
                  {item.error_message || '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!items.length && !calls.isFetching ? (
        <Card className="p-8 text-center text-sm text-mist">{hasFilter ? '没有符合条件的记录' : '暂无调用记录'}</Card>
      ) : null}
    </div>
  )
}

export default McpCallsPage
