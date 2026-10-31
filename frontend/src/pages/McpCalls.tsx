import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type McpCallLogDetail, type McpCallLogItem } from '../lib/api'
import { errorMessage, formatTime } from '../lib/utils'
import { type Column, DataView, EmptyState } from '../components/data-view'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { FilterBar } from '../components/filter-bar'
import { McpCallStatsPanel } from '../components/McpCallStats'

const PAGE_SIZES = [25, 50, 100]

function transportLabel(value: string | null | undefined) {
  if (value === 'mcp') return 'MCP'
  if (value === 'rest') return 'REST'
  return '—'
}

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
  {
    key: 'capability',
    header: '能力 / 操作',
    cell: (item) => (
      <div className="min-w-0">
        <div className="text-paper">{item.capability_id}</div>
        <div className="font-mono text-xs text-mist">{item.operation}</div>
      </div>
    ),
  },
  {
    key: 'transport',
    header: '来源',
    className: 'whitespace-nowrap text-mist',
    cell: (item) => transportLabel(item.transport),
  },
  {
    key: 'result',
    header: '结果',
    className: 'whitespace-nowrap',
    cell: (item) => (
      <div className="flex flex-col items-start gap-1">
        <Badge tone={item.success ? 'ok' : 'bad'}>{item.success ? '成功' : '失败'}</Badge>
        {item.http_status != null ? <span className="font-mono text-xs text-mist">HTTP {item.http_status}</span> : null}
      </div>
    ),
  },
  { key: 'latency', header: '耗时', className: 'whitespace-nowrap', cell: (item) => `${item.latency_ms} ms` },
  {
    key: 'client',
    header: '客户端',
    cell: (item) =>
      item.client_ip || item.client_name ? (
        <div className="min-w-0">
          <div className="font-mono text-xs text-paper">{item.client_ip || '—'}</div>
          <div className="text-xs text-mist">{item.client_name || '—'}</div>
        </div>
      ) : (
        <span className="text-mist">—</span>
      ),
  },
  {
    key: 'error',
    header: '错误',
    className: 'max-w-[18rem]',
    cell: (item) =>
      item.error_message ? (
        <div className="line-clamp-2 break-words text-mist [overflow-wrap:anywhere]" title={item.error_message}>
          {item.error_message}
        </div>
      ) : (
        <span className="text-mist">-</span>
      ),
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
  const [detailId, setDetailId] = useState<number | null>(null)
  const [days, setDays] = useState(7)

  const catalog = useQuery({ queryKey: ['mcp-catalog', 'all'], queryFn: () => api.mcpCatalog('all') })
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

  const stats = useQuery({
    queryKey: ['mcp-call-stats', { days, capabilityId, keyId, result, keyword }],
    queryFn: () =>
      api.mcpCallStats({
        days,
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
      <p className="text-sm text-mist">
        授权通过后的能力调用都会记录，成功与失败均可查，点击任意一行查看请求与响应详情。当前筛选共 {total} 条。
      </p>
      <McpCallStatsPanel stats={stats.data} days={days} onDays={setDays} loading={stats.isPending} />

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
        onRowClick={(item) => setDetailId(item.id)}
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
              <span>{transportLabel(item.transport)}</span>
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

      {detailId != null ? <CallDetailDialog id={detailId} onClose={() => setDetailId(null)} /> : null}
    </div>
  )
}

function pretty(text: string | null | undefined): string {
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-xs uppercase tracking-[0.12em] text-mist">{title}</div>
      {children}
    </div>
  )
}

function CallDetailDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['mcp-call', id],
    queryFn: () => api.mcpCall(id),
  })

  return (
    <Dialog title="调用详情" onClose={onClose} className="max-w-3xl">
      {isLoading ? <div className="text-sm text-mist">加载中…</div> : null}
      {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载失败')}</div> : null}
      {data ? <CallDetailBody detail={data} /> : null}
    </Dialog>
  )
}

function CallDetailBody({ detail }: { detail: McpCallLogDetail }) {
  const hasDetail = Boolean(
    detail.request_body || detail.response_body || detail.request_headers || detail.client_ip || detail.user_agent,
  )
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Meta label="结果" value={detail.success ? '成功' : '失败'} />
        <Meta label="来源" value={transportLabel(detail.transport)} />
        <Meta label="HTTP" value={detail.http_status != null ? String(detail.http_status) : '—'} />
        <Meta label="耗时" value={`${detail.latency_ms} ms`} />
        <Meta label="能力" value={detail.capability_id} />
        <Meta label="操作" value={detail.operation} />
        <Meta label="Key" value={detail.mcp_key_name || '—'} />
        <Meta label="时间" value={formatTime(detail.created_at)} />
      </div>

      {detail.error_message ? (
        <Section title="错误">
          <div className="space-y-1 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {detail.error_type ? <div className="font-mono text-xs">{detail.error_type}</div> : null}
            <div className="break-words [overflow-wrap:anywhere]">{detail.error_message}</div>
          </div>
        </Section>
      ) : null}

      {!hasDetail ? (
        <div className="rounded-md border border-line bg-ink/40 px-3 py-2 text-xs text-mist">
          该记录为较早的历史数据，未采集请求与响应详情（从本次升级后的调用起开始记录）。
        </div>
      ) : (
        <>
          <Section title="客户端">
            <div className="grid grid-cols-1 gap-2 rounded-md border border-line bg-ink/40 px-3 py-2 text-sm sm:grid-cols-3">
              <div>
                <div className="text-xs text-mist">IP</div>
                <div className="break-all font-mono text-xs text-paper [overflow-wrap:anywhere]">{detail.client_ip || '—'}</div>
              </div>
              <div>
                <div className="text-xs text-mist">客户端</div>
                <div className="text-paper">{detail.client_name || '—'}</div>
              </div>
              <div>
                <div className="text-xs text-mist">User-Agent</div>
                <div className="break-all text-xs text-paper [overflow-wrap:anywhere]">{detail.user_agent || '—'}</div>
              </div>
            </div>
          </Section>

          {detail.request_headers ? (
            <Section title="请求头（已脱敏）">
              <pre className="max-h-48 overflow-auto rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
                {pretty(detail.request_headers)}
              </pre>
            </Section>
          ) : null}

          <Section title="请求体">
            <pre className="max-h-64 overflow-auto rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
              {detail.request_body ? pretty(detail.request_body) : '（无请求体）'}
            </pre>
          </Section>

          <Section title="响应体">
            <pre className="max-h-80 overflow-auto rounded-md border border-line bg-ink p-3 font-mono text-xs text-paper [overflow-wrap:anywhere]">
              {detail.response_body ? pretty(detail.response_body) : '（无响应体）'}
            </pre>
          </Section>
        </>
      )}
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line px-3 py-2">
      <div className="text-xs uppercase tracking-[0.14em] text-mist">{label}</div>
      <div className="mt-1 truncate text-paper" title={value}>
        {value}
      </div>
    </div>
  )
}

export default McpCallsPage
