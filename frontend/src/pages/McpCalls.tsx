import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type McpCallLogItem } from '../lib/api'
import { errorMessage, formatTime } from '../lib/utils'
import { type Column, DataView, EmptyState } from '../components/data-view'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { FilterBar } from '../components/filter-bar'

const PAGE_SIZES = [25, 50, 100]

const COLUMNS: Column<McpCallLogItem>[] = [
  { key: 'time', header: '时间', className: 'whitespace-nowrap', cell: (item) => formatTime(item.created_at) },
  {
    key: 'key',
    header: 'Key',
    cell: (item) => (
      <div className="min-w-0">
        <div className="text-paper">{item.mcp_key_name || '-'}</div>
        <div className="font-mono text-xs text-mist">{item.mcp_key_prefix || '-'}</div>
      </div>
    ),
  },
  { key: 'capability', header: '能力', cell: (item) => item.capability_id },
  { key: 'operation', header: '操作', className: 'font-mono text-xs', cell: (item) => item.operation },
  {
    key: 'result',
    header: '结果',
    cell: (item) => <Badge tone={item.success ? 'ok' : 'bad'}>{item.success ? '成功' : '失败'}</Badge>,
  },
  { key: 'latency', header: '耗时', className: 'whitespace-nowrap', cell: (item) => `${item.latency_ms} ms` },
  {
    key: 'error',
    header: '错误',
    className: 'max-w-lg whitespace-pre-wrap break-words text-mist',
    cell: (item) => item.error_message || '-',
  },
]

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
    <div className="space-y-3" aria-busy={calls.isFetching}>
      <p className="text-sm text-mist">授权通过后的能力调用都会记录，成功与失败均可查。当前共 {total} 条。</p>

      <FilterBar activeCount={hasFilter ? 1 : 0} onReset={clearFilters} desktopClassName="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
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
      </FilterBar>

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

      {calls.isError ? <div className="text-sm text-danger" role="alert">{errorMessage(calls.error, '加载失败')}</div> : null}

      <DataView
        items={items}
        columns={COLUMNS}
        keyOf={(item) => String(item.id)}
        loading={calls.isFetching && !items.length}
        empty={
          <EmptyState
            title={hasFilter ? '没有符合条件的记录' : '暂无调用记录'}
            description={hasFilter ? '可以重置筛选后重试' : '授权能力被调用后会在这里留下记录'}
          />
        }
        renderCard={(item) => (
          <Card className="min-w-0 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{item.mcp_key_name || '-'}</div>
                <div className="truncate font-mono text-xs text-mist">{item.mcp_key_prefix || '-'}</div>
              </div>
              <Badge tone={item.success ? 'ok' : 'bad'}>{item.success ? '成功' : '失败'}</Badge>
            </div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist">
              <span className="font-mono">{item.capability_id}</span>
              <span aria-hidden="true">·</span>
              <span className="font-mono">{item.operation}</span>
              <span aria-hidden="true">·</span>
              <span>{item.latency_ms} ms</span>
            </div>
            <div className="text-xs text-mist">{formatTime(item.created_at)}</div>
            {item.error_message ? (
              <div className="line-clamp-3 break-words rounded-md bg-danger/10 px-2 py-1.5 font-mono text-xs text-danger [overflow-wrap:anywhere]">
                {item.error_message}
              </div>
            ) : null}
          </Card>
        )}
      />
    </div>
  )
}

export default McpCallsPage
