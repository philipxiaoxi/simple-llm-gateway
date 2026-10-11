import { useQuery } from '@tanstack/react-query'
import { Download, Gauge, Trophy } from 'lucide-react'
import { useState } from 'react'
import { Pagination } from '../components/Pagination'
import { Button, Card, Select } from '../components/ui'
import { api, type BenchmarkLeaderboardItem, type BenchmarkResult, type BenchmarkRun } from '../lib/api'
import { notifyBad } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

function speed(value?: number | null) {
  return value === null || value === undefined ? '—' : `${value} tok/s`
}

function ms(value?: number | null) {
  return value === null || value === undefined ? '—' : `${value} ms`
}

function ResultTile({ result, rank }: { result: BenchmarkResult; rank?: number }) {
  const tone = result.timeout ? 'text-warn' : result.ok ? 'text-signal' : 'text-danger'
  const label = result.timeout ? '超时' : result.ok ? '成功' : '失败'
  const fastest = rank === 1
  return (
    <div className={cn('min-w-0 border px-3 py-2 text-xs', !result.ok ? 'border-danger/40 bg-danger/5' : fastest ? 'border-signal/50 bg-signal/[0.06]' : 'border-line/70')}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {rank ? (
            <span className={cn('inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[11px]', fastest ? 'bg-signal text-ink' : 'bg-white/10 text-mist')}>
              {rank}
            </span>
          ) : null}
          <span className="min-w-0 truncate font-medium" title={result.model}>{result.model}</span>
        </div>
        <span className={cn('shrink-0', tone)}>{label}</span>
      </div>
      <div className="mt-1 truncate text-mist" title={result.account_name}>{result.account_name} · {result.provider}</div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className={cn('font-mono text-lg font-semibold tabular-nums', result.ok ? 'text-signal' : 'text-mist')}>{result.output_tokens_per_second ?? '—'}</span>
        <span className="text-[10px] uppercase tracking-[0.16em] text-mist">tok/s</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-mist">
        <span>首 token {ms(result.first_token_ms)}</span>
        <span>总耗时 {ms(result.total_ms)}</span>
      </div>
      {result.error ? (
        <div className="mt-1 break-all text-danger [overflow-wrap:anywhere]" title={result.error}>{result.error}</div>
      ) : result.preview ? (
        <div className="mt-1 break-all text-mist [overflow-wrap:anywhere]" title={result.preview}>{result.preview}</div>
      ) : null}
    </div>
  )
}

const MEDALS = ['🥇', '🥈', '🥉']

function RankBadge({ rank }: { rank: number }) {
  const medal = MEDALS[rank - 1]
  if (medal) return <span className="text-lg leading-none" title={`第 ${rank} 名`}>{medal}</span>
  return (
    <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-white/5 px-1.5 font-mono text-xs text-mist">
      {rank}
    </span>
  )
}

function rankedResults(results: BenchmarkResult[]) {
  let rank = 0
  return [...results]
    .sort((a, b) => (b.output_tokens_per_second ?? -1) - (a.output_tokens_per_second ?? -1))
    .map((result) => {
      const hasSpeed = result.output_tokens_per_second !== null && result.output_tokens_per_second !== undefined
      if (hasSpeed) rank += 1
      return { result, rank: hasSpeed ? rank : undefined }
    })
}

function ExportMenu() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const stamp = new Date().toISOString().slice(0, 10)
  async function run(kind: 'history' | 'leaderboard') {
    setOpen(false)
    setBusy(true)
    try {
      const blob = kind === 'history' ? await api.exportBenchmarkHistory() : await api.exportBenchmarkLeaderboard()
      downloadBlob(blob, `benchmark-${kind}-${stamp}.csv`)
    } catch (error) {
      notifyBad(errorMessage(error, '导出失败'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="relative shrink-0">
      <Button type="button" variant="line" className="w-full sm:w-auto" disabled={busy} onClick={() => setOpen((value) => !value)}>
        <Download size={16} />
        导出
      </Button>
      {open ? (
        <>
          <button type="button" aria-label="关闭导出菜单" className="fixed inset-0 z-40 cursor-default" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-1 w-44 border border-line bg-panel-2 py-1 shadow-[0_12px_32px_rgba(0,0,0,0.45)]">
            <button type="button" className="block w-full px-3 py-2 text-left text-sm text-paper hover:bg-white/5" onClick={() => void run('history')}>
              导出测速历史
            </button>
            <button type="button" className="block w-full px-3 py-2 text-left text-sm text-paper hover:bg-white/5" onClick={() => void run('leaderboard')}>
              导出速度排行榜
            </button>
          </div>
        </>
      ) : null}
    </div>
  )
}

function Leaderboard({ items, isLoading }: { items: BenchmarkLeaderboardItem[]; isLoading: boolean }) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <Trophy size={17} className="text-signal" />
        <h2 className="font-semibold">速度排行榜</h2>
        <span className="text-xs text-mist">按账号 × 模型的平均输出速度排名</span>
      </div>
      <div className="hidden overflow-x-auto lg:block">
        <table className="w-full text-left text-sm">
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="px-4 py-3 font-medium">#</th>
              <th className="px-4 py-3 font-medium">模型</th>
              <th className="px-4 py-3 font-medium">账号</th>
              <th className="px-4 py-3 font-medium">供应商</th>
              <th className="px-4 py-3 font-medium">平均速度</th>
              <th className="px-4 py-3 font-medium">最佳</th>
              <th className="px-4 py-3 font-medium">样本</th>
              <th className="px-4 py-3 font-medium">平均首 token</th>
              <th className="px-4 py-3 font-medium">最近测试</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.account_id}-${item.model}`} className="border-t border-line">
                <td className="px-4 py-3">
                  <RankBadge rank={item.rank} />
                </td>
                <td className="max-w-[220px] truncate px-4 py-3 font-medium" title={item.model}>{item.model}</td>
                <td className="max-w-[180px] truncate px-4 py-3 text-mist" title={item.account_name}>{item.account_name}</td>
                <td className="px-4 py-3 text-mist">{item.provider}</td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-signal">{speed(item.avg_speed)}</td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-mist">{speed(item.best_speed)}</td>
                <td className="px-4 py-3 text-mist">{item.samples}</td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-mist">{ms(item.avg_first_token_ms)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-mist">{item.last_tested_at ? formatTime(item.last_tested_at) : '—'}</td>
              </tr>
            ))}
            {!isLoading && !items.length ? (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-sm text-mist">还没有可排名的测速数据，先去测速并保存结果</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="grid gap-2 p-3 lg:hidden">
        {items.map((item) => (
          <div key={`${item.account_id}-${item.model}`} className="flex items-center gap-3 border border-line p-3">
            <span className="flex w-7 shrink-0 items-center justify-center">
              <RankBadge rank={item.rank} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium" title={item.model}>{item.model}</div>
              <div className="truncate text-xs text-mist" title={item.account_name}>{item.account_name} · {item.provider}</div>
            </div>
            <div className="shrink-0 text-right">
              <div className="font-mono text-sm text-signal">{speed(item.avg_speed)}</div>
              <div className="text-[11px] text-mist">{item.samples} 次</div>
            </div>
          </div>
        ))}
        {!isLoading && !items.length ? <div className="px-3 py-8 text-center text-sm text-mist">还没有可排名的测速数据</div> : null}
      </div>
    </Card>
  )
}

export function BenchmarkHistoryPage() {
  const [page, setPage] = useState(1)
  const [groupFilter, setGroupFilter] = useState('')
  const pageSize = 10
  const { data: groupsData } = useQuery({ queryKey: ['benchmark-groups'], queryFn: api.benchmarkGroups })
  const groups = groupsData?.items ?? []
  const { data: leaderboard, isLoading: leaderboardLoading } = useQuery({
    queryKey: ['benchmark-leaderboard'],
    queryFn: api.benchmarkLeaderboard,
  })
  const { data, isLoading } = useQuery({
    queryKey: ['benchmark-history', page, groupFilter],
    queryFn: () => api.benchmarkHistory(page, pageSize, groupFilter ? Number(groupFilter) : undefined, true),
  })
  const runs = data?.items ?? []
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))

  function goToPage(next: number) {
    const bounded = Math.min(Math.max(next, 1), pageCount)
    if (bounded === page) return
    setPage(bounded)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0 lg:flex-1">
          <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK ARCHIVE</div>
          <h1 className="text-2xl font-semibold">测速历史</h1>
          <p className="mt-1 text-sm text-mist">速度排行榜与全部保存的测速会话，结果直接平铺展示。</p>
        </div>
        <ExportMenu />
      </div>

      <Leaderboard items={leaderboard?.items ?? []} isLoading={leaderboardLoading} />

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="font-semibold">测速会话</h2>
        <div className="flex items-center gap-2">
          <span className="shrink-0 text-xs text-mist">分组</span>
          <Select
            className="w-full sm:w-56"
            value={groupFilter}
            onChange={(event) => {
              setGroupFilter(event.target.value)
              setPage(1)
            }}
          >
            <option value="">全部分组</option>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>{group.name}</option>
            ))}
          </Select>
        </div>
      </div>

      <div className="space-y-4">
        {runs.map((run: BenchmarkRun) => (
          <Card key={run.id} className="min-w-0 space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{formatTime(run.created_at)}</span>
                  {run.group_name ? (
                    <span className="rounded-full bg-info/15 px-2 py-0.5 text-[11px] text-info">{run.group_name}</span>
                  ) : null}
                </div>
                <div className="mt-1 break-all text-xs text-mist [overflow-wrap:anywhere]" title={run.prompt}>{run.prompt}</div>
              </div>
              <span className="shrink-0 font-mono text-xs text-mist">{run.success_count} / {run.result_count} 成功</span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {rankedResults(run.results ?? []).map(({ result, rank }) => (
                <ResultTile key={`${result.account_id}-${result.provider}-${result.model}`} result={result} rank={rank} />
              ))}
            </div>
          </Card>
        ))}
        {!isLoading && !runs.length ? (
          <Card>
            <div className="px-4 py-12 text-center text-sm text-mist">暂无已保存的测速结果</div>
          </Card>
        ) : null}
      </div>

      <Pagination page={page} pageCount={pageCount} total={data?.total ?? 0} unit="次" onPage={goToPage} />
      <div className="flex items-center gap-2 text-xs text-mist">
        <Gauge size={14} />
        每次在模型测速页完成测速后，会自动保存一组会话到这里。
      </div>
    </div>
  )
}
