import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Download, Gauge } from 'lucide-react'
import { useState } from 'react'
import { Pagination } from '../components/Pagination'
import { Button, Card, Dialog } from '../components/ui'
import { api, type BenchmarkRun } from '../lib/api'
import { notifyBad } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

function downloadBlob(blob: Blob) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `benchmark-history-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

function RunDetails({ runId }: { runId: number }) {
  const { data, isLoading } = useQuery({ queryKey: ['benchmark-run', runId], queryFn: () => api.benchmarkRun(runId) })
  if (isLoading) return <div className="px-4 py-4 text-sm text-mist">加载结果中...</div>
  return (
    <div className="space-y-3">
      <div className="break-all text-xs text-mist [overflow-wrap:anywhere]">提示词：{data?.prompt}</div>
      <div className="grid gap-2 lg:grid-cols-2 xl:grid-cols-3">
        {(data?.results ?? []).map((result) => (
          <div key={`${result.account_id}-${result.provider}-${result.model}`} className="min-w-0 border border-line/70 px-3 py-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate font-medium" title={result.model}>{result.model}</span>
              <span className={result.timeout ? 'text-warn' : result.ok ? 'text-signal' : 'text-danger'}>{result.timeout ? '超时' : result.ok ? '成功' : '失败'}</span>
            </div>
            <div className="mt-1 truncate text-mist">{result.account_name} · {result.provider}</div>
            <div className="mt-1 font-mono text-mist">首 token {result.first_token_ms ?? '—'} ms · 总耗时 {result.total_ms ?? '—'} ms · {result.output_tokens_per_second ?? '—'} tok/s</div>
            {result.error ? <div className="mt-1 break-all text-danger [overflow-wrap:anywhere]" title={result.error}>{result.error}</div> : null}
          </div>
        ))}
      </div>
    </div>
  )
}

export function BenchmarkHistoryPage() {
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<number | null>(null)
  const pageSize = 20
  const { data, isLoading } = useQuery({ queryKey: ['benchmark-history', page], queryFn: () => api.benchmarkHistory(page, pageSize) })
  const runs = data?.items ?? []
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))

  async function exportHistory() {
    try { downloadBlob(await api.exportBenchmarkHistory()) } catch (error) { notifyBad(errorMessage(error, '导出测速结果失败')) }
  }

  function goToPage(next: number) {
    const bounded = Math.min(Math.max(next, 1), pageCount)
    if (bounded === page) return
    setPage(bounded)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK ARCHIVE</div>
            <h1 className="text-2xl font-semibold">测速历史</h1>
            <p className="mt-1 text-sm text-mist">查看已保存的测速会话和每个模型的结果。</p>
          </div>
          <Button type="button" variant="line" className="shrink-0 lg:hidden" onClick={() => void exportHistory()}>
            <Download size={16} />
            导出
          </Button>
        </div>
        <div className="hidden lg:block">
          <Button type="button" variant="line" onClick={() => void exportHistory()}>
            <Download size={16} />
            导出 CSV
          </Button>
        </div>
      </div>
      <Card className="overflow-hidden p-0">
        <div className="hidden overflow-x-auto lg:block">
          <table className="w-full text-left text-sm">
            <thead className="bg-panel-2 text-mist">
              <tr>
                <th className="px-4 py-3 font-medium">时间</th>
                <th className="px-4 py-3 font-medium">提示词</th>
                <th className="px-4 py-3 font-medium">模型数</th>
                <th className="px-4 py-3 font-medium">成功</th>
                <th className="px-4 py-3 font-medium">输出 token</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run: BenchmarkRun) => (
                <tr key={run.id} className="border-t border-line hover:bg-white/5">
                  <td className="whitespace-nowrap px-4 py-3 text-mist">{formatTime(run.created_at)}</td>
                  <td className="max-w-[420px] truncate px-4 py-3" title={run.prompt}>{run.prompt}</td>
                  <td className="px-4 py-3">{run.result_count}</td>
                  <td className="px-4 py-3">{run.success_count} / {run.result_count}</td>
                  <td className="px-4 py-3">
                    <Button variant="ghost" onClick={() => setExpanded(run.id)}>
                      <ChevronRight size={16} />
                      查看结果
                    </Button>
                  </td>
                </tr>
              ))}
              {!isLoading && !runs.length ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-sm text-mist">暂无已保存的测速结果</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="grid gap-3 p-3 lg:hidden">
          {runs.map((run) => (
            <button key={run.id} type="button" className="min-w-0 border border-line p-3 text-left" onClick={() => setExpanded(run.id)}>
              <div className="flex items-start justify-between gap-3">
                <span className="min-w-0 truncate font-medium">{formatTime(run.created_at)}</span>
                <ChevronRight size={16} className="shrink-0 text-mist" />
              </div>
              <div className="mt-2 truncate text-sm" title={run.prompt}>{run.prompt}</div>
              <div className="mt-1 text-xs text-mist">{run.success_count} / {run.result_count} 成功 · {run.result_count} 模型</div>
            </button>
          ))}
          {!isLoading && !runs.length ? <div className="px-3 py-10 text-center text-sm text-mist">暂无已保存的测速结果</div> : null}
        </div>
      </Card>
      {expanded ? (
        <Dialog
          title={`测速结果 · ${formatTime(runs.find((run) => run.id === expanded)?.created_at ?? '')}`}
          onClose={() => setExpanded(null)}
          className="my-6 max-h-[70vh] max-w-5xl overflow-y-auto lg:my-[15vh]"
        >
          <RunDetails runId={expanded} />
        </Dialog>
      ) : null}
      <Pagination page={page} pageCount={pageCount} total={data?.total ?? 0} unit="次" onPage={goToPage} />
      <div className="flex items-center gap-2 text-xs text-mist">
        <Gauge size={14} />
        保存测速页的当前结果后，这里会自动记录一组测试会话。
      </div>
    </div>
  )
}
