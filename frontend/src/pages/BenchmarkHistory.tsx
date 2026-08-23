import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Download, Gauge } from 'lucide-react'
import { useState } from 'react'
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
      <div className="text-xs text-mist">提示词：{data?.prompt}</div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {(data?.results ?? []).map((result) => (
          <div key={`${result.account_id}-${result.provider}-${result.model}`} className="border border-line/70 px-3 py-2 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium" title={result.model}>{result.model}</span>
              <span className={result.timeout ? 'text-warning' : result.ok ? 'text-success' : 'text-danger'}>{result.timeout ? '超时' : result.ok ? '成功' : '失败'}</span>
            </div>
            <div className="mt-1 truncate text-mist">{result.account_name} · {result.provider}</div>
            <div className="mt-1 font-mono text-mist">首 token {result.first_token_ms ?? '—'} ms · 总耗时 {result.total_ms ?? '—'} ms</div>
            {result.error ? <div className="mt-1 truncate text-danger" title={result.error}>{result.error}</div> : null}
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

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK ARCHIVE</div>
          <h1 className="text-2xl font-semibold">测速历史</h1>
          <p className="mt-1 text-sm text-mist">查看已保存的测速会话和每个模型的结果。</p>
        </div>
        <Button variant="line" onClick={() => void exportHistory()}><Download size={16} />导出 CSV</Button>
      </div>
      <Card className="overflow-hidden p-0">
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full text-left text-sm">
            <thead className="bg-panel-2 text-mist"><tr><th className="px-4 py-3 font-medium">时间</th><th className="px-4 py-3 font-medium">提示词</th><th className="px-4 py-3 font-medium">模型数</th><th className="px-4 py-3 font-medium">成功</th><th className="px-4 py-3 font-medium">输出 token</th></tr></thead>
            <tbody>{runs.map((run: BenchmarkRun) => <tr key={run.id} className="border-t border-line hover:bg-white/5"><td className="px-4 py-3 text-mist">{formatTime(run.created_at)}</td><td className="max-w-[420px] truncate px-4 py-3" title={run.prompt}>{run.prompt}</td><td className="px-4 py-3">{run.result_count}</td><td className="px-4 py-3">{run.success_count} / {run.result_count}</td><td className="px-4 py-3"><Button variant="ghost" onClick={() => setExpanded(expanded === run.id ? null : run.id)}>{expanded === run.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}查看结果</Button></td></tr>)}{!isLoading && !runs.length ? <tr><td colSpan={5} className="px-4 py-12 text-center text-sm text-mist">暂无已保存的测速结果</td></tr> : null}</tbody>
          </table>
        </div>
        <div className="grid gap-3 p-3 md:hidden">{runs.map((run) => <div key={run.id} className="border border-line"><button type="button" className="w-full p-3 text-left" onClick={() => setExpanded(run.id)}><div className="flex items-center justify-between"><span className="font-medium">{formatTime(run.created_at)}</span><ChevronRight size={16} /></div><div className="mt-2 truncate text-sm">{run.prompt}</div><div className="mt-1 text-xs text-mist">{run.success_count} / {run.result_count} 成功</div></button></div>)}</div>
      </Card>
      {expanded ? (
        <Dialog
          title={`测速结果 · ${formatTime(runs.find((run) => run.id === expanded)?.created_at ?? '')}`}
          onClose={() => setExpanded(null)}
          className="max-h-[70vh] max-w-5xl overflow-y-auto"
        >
          <RunDetails runId={expanded} />
        </Dialog>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3"><div className="text-sm text-mist">共 {data?.total ?? 0} 次 · 第 {page} / {pageCount} 页</div><div className="flex gap-2"><Button variant="line" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>上一页</Button><Button variant="line" disabled={page >= pageCount} onClick={() => setPage((current) => current + 1)}>下一页</Button></div></div>
      <div className="flex items-center gap-2 text-xs text-mist"><Gauge size={14} />保存测速页的当前结果后，这里会自动记录一组测试会话。</div>
    </div>
  )
}
