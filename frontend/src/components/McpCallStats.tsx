import { lazy, Suspense, useMemo, useState } from 'react'

import { ChartCard, ChartEmpty, ChartTabs, type ChartTable } from './charts/chart-card'
import {
  CHART_PALETTE,
  CHART_TOOLTIP_BASE,
  categoryAxis,
  compactCount,
  tooltipHtml,
  valueAxis,
  type ChartOption,
  type ChartTooltipParam,
} from '../lib/chart-theme'
import type { McpCallStats, McpCallStatsBucket } from '../lib/api'
import { formatCount } from '../lib/utils'

const EChart = lazy(() => import('./charts/echart').then((module) => ({ default: module.EChart })))

const RANGES = [
  { value: 1, label: '今天' },
  { value: 7, label: '7 天' },
  { value: 30, label: '30 天' },
]

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

function tableOf(items: McpCallStatsBucket[], label: string): ChartTable {
  return {
    head: [label, '调用', '成功', '失败', '平均耗时'],
    rows: items.map((item) => [item.label, item.total, item.success, item.failed, `${item.avg_latency_ms} ms`]),
  }
}

function barOption(items: McpCallStatsBucket[]): ChartOption {
  return {
    color: [CHART_PALETTE[0], CHART_PALETTE[11]],
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
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
    <Suspense fallback={<div className="h-[220px] animate-pulse rounded-lg bg-white/[0.03]" />}>
      <EChart option={option} ariaLabel={label} className="h-[220px]" />
    </Suspense>
  )
}

function lineOption(items: McpCallStatsBucket[]): ChartOption {
  return {
    color: [CHART_PALETTE[0], CHART_PALETTE[11]],
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
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

export function McpCallStatsPanel({
  stats,
  days,
  onDays,
  loading,
}: {
  stats?: McpCallStats
  days: number
  onDays: (days: number) => void
  loading: boolean
}) {
  const [breakdown, setBreakdown] = useState<'capability' | 'operation' | 'key'>('capability')
  const breakdownItems = useMemo(() => {
    if (!stats) return []
    if (breakdown === 'operation') return stats.operations
    if (breakdown === 'key') return stats.keys
    return stats.capabilities
  }, [breakdown, stats])
  const empty = !stats || stats.total === 0

  return (
    <section className="space-y-3" aria-label="调用统计">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium text-paper">调用概览</div>
        <div className="flex gap-1">
          {RANGES.map((range) => (
            <button
              key={range.value}
              type="button"
              onClick={() => onDays(range.value)}
              className={`rounded-full border px-3 py-1 text-xs ${
                days === range.value ? 'border-signal/50 bg-signal/15 text-signal' : 'border-line text-mist hover:text-paper'
              }`}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 lg:gap-3">
        <StatCard label="调用次数" value={loading && !stats ? '…' : formatCount(stats?.total ?? 0)} hint={`近 ${days} 天`} />
        <StatCard label="成功" value={formatCount(stats?.success ?? 0)} hint={stats ? `成功率 ${percent(stats.success_rate)}` : undefined} />
        <StatCard label="失败" value={formatCount(stats?.failed ?? 0)} hint={stats?.failed ? '点击列表可看错误详情' : '暂无失败'} />
        <StatCard label="平均耗时" value={`${stats?.avg_latency_ms ?? 0} ms`} hint={`P95 ${stats?.p95_latency_ms ?? 0} ms`} />
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <ChartCard title="调用趋势" description="按天统计成功与失败" table={stats ? tableOf(stats.daily, '日期') : undefined}>
          {empty ? <ChartEmpty title="当前范围内没有调用" /> : <ChartBody option={lineOption(stats.daily)} label="调用趋势" />}
        </ChartCard>
        <ChartCard title="24 小时分布" description="按小时汇总当前范围" table={stats ? tableOf(stats.hourly.filter((item) => item.total), '时段') : undefined}>
          {empty ? <ChartEmpty title="当前范围内没有调用" /> : <ChartBody option={barOption(stats.hourly)} label="24小时调用分布" />}
        </ChartCard>
      </div>

      <ChartCard
        title="调用来源"
        description="按能力、操作或 Key 汇总"
        actions={
          <ChartTabs
            label="调用来源维度"
            value={breakdown}
            onChange={setBreakdown}
            options={[
              { value: 'capability', label: '能力' },
              { value: 'operation', label: '操作' },
              { value: 'key', label: 'Key' },
            ]}
          />
        }
        table={tableOf(breakdownItems, breakdown === 'key' ? 'Key' : breakdown === 'operation' ? '操作' : '能力')}
      >
        {breakdownItems.length ? (
          <ChartBody option={barOption(breakdownItems)} label="调用来源分布" />
        ) : (
          <ChartEmpty title="当前范围内没有调用" />
        )}
      </ChartCard>
    </section>
  )
}
