import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Pagination } from '../components/Pagination'
import { Badge, Button, Card, Field, Select } from '../components/ui'
import { api, type ContentAuditFinding, type ContentAuditSummary } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { LOG_PAGE_SIZE, errorMessage, formatTime } from '../lib/utils'

const categoryLabel: Record<string, string> = {
  sensitive: '敏感词',
  pii: 'PII',
  secret: '密钥',
}

const severityLabel: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

function statusTone(status: string): 'ok' | 'bad' | 'warn' | 'mist' {
  if (status === 'running') return 'warn'
  if (status === 'failed') return 'bad'
  if (status === 'partial') return 'warn'
  if (status === 'ok') return 'ok'
  return 'mist'
}

function statusLabel(summary: ContentAuditSummary) {
  if (summary.status === 'running') return '运行中'
  if (summary.status === 'failed') return '失败'
  if (summary.status === 'partial') return '部分成功'
  if (summary.status === 'ok') return '成功'
  return '未运行'
}

function severityTone(severity: string): 'ok' | 'bad' | 'warn' | 'mist' {
  if (severity === 'high') return 'bad'
  if (severity === 'medium') return 'warn'
  return 'mist'
}

function categoryTone(category: string): 'ok' | 'bad' | 'warn' | 'mist' | 'info' {
  if (category === 'secret') return 'bad'
  if (category === 'pii') return 'warn'
  if (category === 'sensitive') return 'info'
  return 'mist'
}

function findingHref(item: ContentAuditFinding) {
  const params = new URLSearchParams()
  if (item.message_seq >= 0) params.set('seq', String(item.message_seq))
  params.set('hl', `${item.start_offset}-${item.end_offset}`)
  return `/logs/${item.log_id}?${params}`
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="px-4 py-10 text-center text-sm text-mist">
      {filtered ? '当前筛选没有命中项' : '尚无命中项，可到定时任务页执行内容审计扫描'}
    </div>
  )
}

export function ContentAuditPage() {
  const queryClient = useQueryClient()
  const { data: keys = [] } = useQuery({ queryKey: ['keys'], queryFn: () => api.keys() })
  const { data: summary } = useQuery({
    queryKey: ['content-audit-summary'],
    queryFn: api.contentAuditSummary,
    refetchInterval: (query) => (query.state.data?.running ? 4000 : 15000),
  })
  const syncLexicon = useMutation({
    mutationFn: api.syncContentAuditLexicon,
    onSuccess: (payload) => {
      void queryClient.invalidateQueries({ queryKey: ['content-audit-summary'] })
      notifyOk(`词库已同步，${payload.word_count} 个词，${payload.categories.length} 个分类`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '词库同步失败')),
  })
  const runScan = useMutation({
    mutationFn: () => api.runJob('content_audit'),
    onSuccess: (payload) => {
      void queryClient.invalidateQueries({ queryKey: ['content-audit-summary'] })
      void queryClient.invalidateQueries({ queryKey: ['content-audit-findings'] })
      const current = payload.items.find((item) => item.id === 'content_audit')
      if (current?.error_message) notifyBad(current.error_message)
      else notifyOk(current?.last_message || '扫描已完成')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '扫描失败')),
  })
  const [category, setCategory] = useState('')
  const [lexiconCategory, setLexiconCategory] = useState('')
  const [severity, setSeverity] = useState('')
  const [keyId, setKeyId] = useState('')
  const [page, setPage] = useState(1)

  const query = {
    category: category || undefined,
    lexicon_category: lexiconCategory || undefined,
    severity: severity || undefined,
    api_key_id: keyId ? Number(keyId) : undefined,
    page,
    page_size: LOG_PAGE_SIZE,
  }
  const { data, isPending } = useQuery({
    queryKey: ['content-audit-findings', query],
    queryFn: () => api.contentAuditFindings(query),
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE))
  const filtered = Boolean(category || lexiconCategory || severity || keyId)
  const lexiconCategories = summary?.lexicon_categories ?? []

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
        <h1 className="text-2xl font-semibold">内容审计</h1>
        <p className="mt-1 text-sm text-mist">事后扫描请求正文，只列出敏感词、PII 与密钥命中。</p>
      </div>

      <Card className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={summary ? statusTone(summary.status) : 'mist'}>{summary ? statusLabel(summary) : '加载中'}</Badge>
              <span className="text-sm text-mist">
                已扫 {summary?.scanned_count ?? '—'} / {summary?.total_logs ?? '—'} 条请求 · 命中 {summary?.finding_count ?? '—'}
              </span>
              <Link to="/jobs" className="text-sm text-signal hover:underline">
                定时任务
              </Link>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-mist lg:flex lg:flex-wrap lg:gap-x-4">
              <span>上次完成 {formatTime(summary?.last_finished_at)}</span>
              <span>词库 {summary?.lexicon_word_count ?? '—'} 条 · {lexiconCategories.length} 分类</span>
              <span>词库更新 {formatTime(summary?.lexicon_updated_at)}</span>
              <span>剩余 {summary?.remaining ?? '—'}</span>
              <span>敏感词 {summary?.by_category?.sensitive ?? 0}</span>
              <span>PII {summary?.by_category?.pii ?? 0}</span>
              <span>密钥 {summary?.by_category?.secret ?? 0}</span>
            </div>
          </div>
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
            <Button
              type="button"
              variant="line"
              className="w-full sm:w-auto"
              disabled={runScan.isPending || summary?.running}
              onClick={() => runScan.mutate()}
            >
              <Play size={16} className={runScan.isPending ? 'animate-pulse' : undefined} />
              {runScan.isPending || summary?.running ? '扫描中…' : '立即扫描'}
            </Button>
            <Button
              type="button"
              variant="line"
              className="w-full sm:w-auto"
              disabled={syncLexicon.isPending}
              onClick={() => syncLexicon.mutate()}
            >
              <RefreshCw size={16} className={syncLexicon.isPending ? 'animate-spin' : undefined} />
              {syncLexicon.isPending ? '同步中…' : '同步词库'}
            </Button>
          </div>
        </div>
        {summary?.error_message ? <div className="text-sm text-danger">{summary.error_message}</div> : null}
        {summary?.last_message && !summary.error_message ? <div className="text-sm text-mist">{summary.last_message}</div> : null}
      </Card>

      <Card className="grid grid-cols-2 gap-3 lg:flex lg:flex-nowrap lg:items-end">
        <div className="min-w-0 lg:w-32">
          <Field label="类别">
            <Select value={category} onChange={changeFilter(setCategory)}>
              <option value="">全部类别</option>
              <option value="sensitive">敏感词</option>
              <option value="pii">PII</option>
              <option value="secret">密钥</option>
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-40">
          <Field label="敏感词分类">
            <Select value={lexiconCategory} onChange={changeFilter(setLexiconCategory)}>
              <option value="">全部分类</option>
              {lexiconCategories.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="min-w-0 lg:w-28">
          <Field label="严重级别">
            <Select value={severity} onChange={changeFilter(setSeverity)}>
              <option value="">全部级别</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
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
      </Card>

      <div className="hidden overflow-x-auto rounded-xl border border-line lg:block">
        <table className="w-full min-w-[1080px] text-left text-sm">
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="whitespace-nowrap px-3 py-2 font-medium">时间</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">类别</th>
              <th className="px-3 py-2 font-medium">规则</th>
              <th className="px-3 py-2 font-medium">摘录</th>
              <th className="px-3 py-2 font-medium">Key</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">记录</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">级别</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t border-line hover:bg-white/5">
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-mist">{formatTime(item.created_at)}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <Badge tone={categoryTone(item.category)}>{categoryLabel[item.category] || item.category}</Badge>
                </td>
                <td className="max-w-[180px] truncate px-3 py-2 font-mono text-xs" title={item.rule_key}>
                  {item.lexicon_category ? `${item.lexicon_category} / ${item.rule_key}` : item.rule_key}
                </td>
                <td className="max-w-[320px] truncate px-3 py-2" title={item.excerpt}>
                  <Link to={findingHref(item)} className="text-paper hover:text-signal">
                    {item.excerpt}
                  </Link>
                </td>
                <td className="max-w-[160px] truncate px-3 py-2" title={item.api_key_name || undefined}>
                  {item.api_key_name || '—'}
                </td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                  <Link to={findingHref(item)} className="text-signal hover:underline">
                    #{item.log_id}
                  </Link>
                </td>
                <td className="whitespace-nowrap px-3 py-2">
                  <Badge tone={severityTone(item.severity)}>{severityLabel[item.severity] || item.severity}</Badge>
                </td>
              </tr>
            ))}
            {!isPending && items.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <EmptyState filtered={filtered} />
                </td>
              </tr>
            ) : null}
            {isPending && items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-sm text-mist">
                  加载中…
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:hidden">
        {items.map((item) => (
          <Link key={item.id} to={findingHref(item)} className="block min-h-11">
            <Card className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-medium" title={item.rule_key}>
                    {item.lexicon_category ? `${item.lexicon_category} / ${item.rule_key}` : item.rule_key}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-mist">{item.api_key_name || '—'} · #{item.log_id}</div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Badge tone={categoryTone(item.category)}>{categoryLabel[item.category] || item.category}</Badge>
                  <Badge tone={severityTone(item.severity)}>{severityLabel[item.severity] || item.severity}</Badge>
                </div>
              </div>
              <div className="line-clamp-3 text-sm text-paper">{item.excerpt}</div>
              <div className="text-xs text-mist">{formatTime(item.created_at)}</div>
            </Card>
          </Link>
        ))}
        {isPending && items.length === 0 ? <div className="px-4 py-10 text-center text-sm text-mist">加载中…</div> : null}
        {!isPending && items.length === 0 ? (
          <Card>
            <EmptyState filtered={filtered} />
          </Card>
        ) : null}
      </div>

      <Pagination page={page} pageCount={pageCount} total={total} unit="条命中" onPage={goToPage} />
    </div>
  )
}
