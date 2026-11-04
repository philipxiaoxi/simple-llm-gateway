import { lazy, Suspense } from 'react'

import { ChartCard, ChartEmpty, type ChartTable } from './charts/chart-card'
import {
  categoryAxis,
  chartTheme,
  compactCount,
  formatPercent,
  tooltipHtml,
  valueAxis,
  type ChartOption,
  type ChartTooltipParam,
} from '../lib/chart-theme'
import type { McpCallStats, McpCallStatsBucket, McpLatencyBucket } from '../lib/api'
import { useTheme } from '../lib/theme'
import { formatCount } from '../lib/utils'

const EChart = lazy(() => import('./charts/echart').then((module) => ({ default: module.EChart })))

const RANGES = [
  { value: 1, label: '今天' },
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
]

/** 趋势/分布/耗时区间共用的计数结构，避免把各类型硬绑在一起 */
type CountBucket = { label: string; total: number; success: number; failed: number }

function percent(value: number) {
  return `${Math.round(value * 1000) / 10}%`
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="text-xs text-mist">{label}</div>
      <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-paper">{value}</div>
      {hint ? <div className="mt-1 text-[11px] text-mist">{hint}</div> : null}
    </div>
  )
}

function seriesTableOf(items: CountBucket[], label: string): ChartTable {
  const total = items.reduce((sum, item) => sum + item.total, 0)
  return {
    head: [label, '调用', '成功', '失败', '占比'],
    rows: items.map((item) => [
      item.label,
      item.total,
      item.success,
      item.failed,
      formatPercent(total ? item.total / total : 0, 1),
    ]),
  }
}

function operationsTableOf(items: McpCallStatsBucket[]): ChartTable {
  return {
    head: ['操作', '调用', 'P95', '均值', '失败'],
    rows: items.map((item) => [
      item.label,
      item.total,
      `${item.p95_latency_ms} ms`,
      `${item.avg_latency_ms} ms`,
      item.failed,
    ]),
  }
}

function failureTableOf(items: McpCallStatsBucket[], failedTotal: number): ChartTable {
  return {
    head: ['操作', '失败', '占失败比例', '失败率', '调用'],
    rows: items.map((item) => [
      item.label,
      item.failed,
      formatPercent(failedTotal ? item.failed / failedTotal : 0, 1),
      formatPercent(item.total ? item.failed / item.total : 0, 1),
      item.total,
    ]),
  }
}

function barOption(items: CountBucket[]): ChartOption {
  return {
    color: [chartTheme().palette[0], chartTheme().palette[11]],
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...chartTheme().tooltipBase,
      trigger: 'axis',
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        return tooltipHtml(
          String(rows[0]?.axisValueLabel ?? ''),
          rows.map((row) => ({
            label: row.seriesName ?? '',
            value: formatCount(Number(row.value ?? 0)),
            color: String(row.color ?? ''),
          })),
        )
      },
    },
    legend: { show: false },
    xAxis: {
      ...categoryAxis(items.map((item) => item.label)),
      axisLabel: {
        ...categoryAxis(items.map((item) => item.label)).axisLabel,
        interval: items.length > 12 ? 2 : 0,
        hideOverlap: true,
      },
    },
    yAxis: valueAxis(compactCount),
    series: [
      { type: 'bar', name: '成功', stack: 'calls', data: items.map((item) => item.success), barMaxWidth: 28 },
      { type: 'bar', name: '失败', stack: 'calls', data: items.map((item) => item.failed), barMaxWidth: 28 },
    ],
  }
}

function ChartBody({ option, label }: { option: ChartOption; label: string }) {
  return (
    <Suspense fallback={<div className="h-[220px] animate-pulse rounded-lg bg-elevate" />}>
      <EChart option={option} ariaLabel={label} className="h-[220px]" />
    </Suspense>
  )
}

function lineOption(items: CountBucket[]): ChartOption {
  return {
    color: [chartTheme().palette[0], chartTheme().palette[11]],
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...chartTheme().tooltipBase,
      trigger: 'axis',
      formatter: (params) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        return tooltipHtml(
          String(rows[0]?.axisValueLabel ?? ''),
          rows.map((row) => ({
            label: row.seriesName ?? '',
            value: formatCount(Number(row.value ?? 0)),
            color: String(row.color ?? ''),
          })),
        )
      },
    },
    xAxis: categoryAxis(items.map((item) => item.label)),
    yAxis: valueAxis(compactCount),
    series: [
      { type: 'line', name: '成功', smooth: true, data: items.map((item) => item.success), showSymbol: false },
      { type: 'line', name: '失败', smooth: true, data: items.map((item) => item.failed), showSymbol: false },
    ],
  }
}

function slowShareOf(buckets: McpLatencyBucket[]) {
  const total = buckets.reduce((sum, item) => sum + item.total, 0)
  const slow = buckets
    .filter((item) => item.key === '1-3s' || item.key === 'gte-3s')
    .reduce((sum, item) => sum + item.total, 0)
  return total ? slow / total : 0
}

/** 慢操作榜：单一服务场景下，最值得看的是哪个操作拖慢了整体 P95 */
function SlowList({ items, emptyHint }: { items: McpCallStatsBucket[]; emptyHint: string }) {
  if (!items.length) {
    return <div className="flex min-h-[180px] items-center justify-center text-xs text-mist-soft">{emptyHint}</div>
  }
  const maxP95 = Math.max(...items.map((item) => item.p95_latency_ms), 1)
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.key}>
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-paper" title={item.label}>
              {item.label}
            </span>
            <span className="shrink-0 font-mono tabular-nums text-signal">P95 {item.p95_latency_ms} ms</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-track">
            <div
              className="h-full rounded-full bg-signal"
              style={{ width: `${Math.max((item.p95_latency_ms / maxP95) * 100, 3)}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-mist">
            <span>{formatCount(item.total)} 次调用</span>
            <span>均值 {item.avg_latency_ms} ms</span>
          </div>
        </li>
      ))}
    </ul>
  )
}

/** 失败集中度：按操作列出失败次数与失败率，比再画一张饼更能定位问题 */
function FailureList({ items, emptyHint }: { items: McpCallStatsBucket[]; emptyHint: string }) {
  if (!items.length) {
    return <div className="flex min-h-[180px] items-center justify-center text-xs text-mist-soft">{emptyHint}</div>
  }
  const maxFailed = Math.max(...items.map((item) => item.failed), 1)
  return (
    <ul className="space-y-2.5">
      {items.map((item) => {
        const rate = item.total ? item.failed / item.total : 0
        return (
          <li key={item.key}>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate text-paper" title={item.label}>
                {item.label}
              </span>
              <span className="shrink-0 font-mono tabular-nums text-mist">
                {formatCount(item.failed)} 次 · {formatPercent(rate, 1)}
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-track">
              <div
                className="h-full rounded-full bg-danger"
                style={{ width: `${Math.max((item.failed / maxFailed) * 100, 3)}%` }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function McpCallStatsPanel({
  stats,
  days,
  onDays,
  loading,
  showRanges = true,
}: {
  stats?: McpCallStats
  days: number
  onDays?: (days: number) => void
  loading: boolean
  showRanges?: boolean
}) {
  // 订阅主题变化，切换后图表配色重新计算
  useTheme()
  const empty = !stats || stats.total === 0
  const buckets = stats?.latency_buckets ?? []
  const slowShare = slowShareOf(buckets)
  const slowCount = buckets.filter((item) => item.key === '1-3s' || item.key === 'gte-3s').reduce((sum, item) => sum + item.total, 0)

  return (
    <section className="space-y-3" aria-label="调用统计">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium text-paper">调用概览</div>
        {showRanges ? (
          <div className="flex gap-1">
            {RANGES.map((range) => (
              <button
                key={range.value}
                type="button"
                onClick={() => onDays?.(range.value)}
                className={`rounded-full border px-3 py-1 text-xs ${
                  days === range.value ? 'border-signal/50 bg-signal/15 text-signal' : 'border-line text-mist hover:text-paper'
                }`}
              >
                {range.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 lg:gap-3">
        <StatCard label="调用次数" value={loading && !stats ? '…' : formatCount(stats?.total ?? 0)} hint={`近 ${days} 天`} />
        <StatCard label="成功" value={formatCount(stats?.success ?? 0)} hint={stats ? `成功率 ${percent(stats.success_rate)}` : undefined} />
        <StatCard label="失败" value={formatCount(stats?.failed ?? 0)} hint={stats?.failed ? '点击列表可看错误详情' : '暂无失败'} />
        <StatCard label="平均耗时" value={`${stats?.avg_latency_ms ?? 0} ms`} hint={`P95 ${stats?.p95_latency_ms ?? 0} ms`} />
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <ChartCard title="调用趋势" description="按天统计成功与失败" table={stats ? seriesTableOf(stats.daily, '日期') : undefined}>
          {empty ? <ChartEmpty title="当前范围内没有调用" /> : <ChartBody option={lineOption(stats.daily)} label="调用趋势" />}
        </ChartCard>
        <ChartCard
          title="24 小时分布"
          description="按小时汇总当前范围"
          table={stats ? seriesTableOf(stats.hourly.filter((item) => item.total), '时段') : undefined}
        >
          {empty ? (
            <ChartEmpty title="当前范围内没有调用" />
          ) : (
            <ChartBody option={barOption(stats.hourly)} label="24小时调用分布" />
          )}
        </ChartCard>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <ChartCard
          title="耗时分布"
          description={
            slowCount
              ? `${formatCount(slowCount)} 次超过 1s，占 ${formatPercent(slowShare, 1)}`
              : '当前范围内没有超过 1s 的调用'
          }
          table={stats ? seriesTableOf(buckets, '耗时区间') : undefined}
        >
          {empty ? <ChartEmpty title="当前范围内没有调用" /> : <ChartBody option={barOption(buckets)} label="调用耗时分布" />}
        </ChartCard>
        <ChartCard
          title="最慢操作"
          description={stats?.slow_operations.length ? '按 P95 耗时排序，定位性能瓶颈' : '当前范围内没有调用'}
          table={stats ? operationsTableOf(stats.slow_operations) : undefined}
        >
          <SlowList items={stats?.slow_operations ?? []} emptyHint="当前范围内没有调用" />
        </ChartCard>
      </div>

      <ChartCard
        title="失败集中"
        description={stats?.failed ? `共 ${formatCount(stats.failed)} 次失败，按操作排序` : '当前范围内没有失败'}
        table={stats ? failureTableOf(stats.failure_operations, stats.failed) : undefined}
      >
        <FailureList items={stats?.failure_operations ?? []} emptyHint="当前范围内没有失败调用" />
      </ChartCard>
    </section>
  )
}
