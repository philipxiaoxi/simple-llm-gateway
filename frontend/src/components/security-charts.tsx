import { useMemo, useState } from 'react'
import { ChartCard, ChartEmpty, ChartTabs, type ChartTable } from './charts/chart-card'
import { EChart } from './charts/echart'
import { SeriesLegend } from './charts/chart-legend'
import {
  CHART_PALETTE,
  CHART_TEXT,
  CHART_TEXT_SOFT,
  CHART_TOOLTIP_BASE,
  areaGradient,
  categoryAxis,
  compactCount,
  dayLabel,
  dayLabelLong,
  formatPercent,
  tooltipHtml,
  valueAxis,
  type ChartOption,
  type ChartTooltipParam,
} from '../lib/chart-theme'
import type { SecurityOverview, SecurityTrendPoint } from '../lib/api'

type ShareMode = 'outcome' | 'client'

const TREND_SERIES = [
  { key: 'login_success', name: '登录成功', color: '#4ade80' },
  { key: 'login_failure', name: '登录失败', color: '#f5b942' },
  { key: 'suspicious', name: '异常请求', color: '#f25f6d' },
] as const

const OUTCOME_ITEMS = [
  { key: 'success', name: '成功', color: '#4ade80' },
  { key: 'rejected', name: '已拒绝', color: '#f5b942' },
  { key: 'failed', name: '失败', color: '#f25f6d' },
  { key: 'unknown', name: '未知', color: '#94a3b8' },
] as const

function paletteColor(index: number) {
  return CHART_PALETTE[((index % CHART_PALETTE.length) + CHART_PALETTE.length) % CHART_PALETTE.length]
}

/** 按 since/until 补齐日期（含首尾），保证无事件的日子也画成 0 而不是被跳过。 */
function datesBetween(since: string, until: string): string[] {
  const start = since.slice(0, 10)
  const end = until.slice(0, 10)
  if (!start || !end) return []
  const cursor = new Date(`${start}T00:00:00Z`)
  const last = new Date(`${end}T00:00:00Z`)
  const dates: string[] = []
  while (cursor <= last) {
    dates.push(cursor.toISOString().slice(0, 10))
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
  return dates
}

function expandTrend(data: SecurityOverview): SecurityTrendPoint[] {
  const byDate = new Map(data.trend.map((point) => [point.date, point]))
  return datesBetween(data.since, data.until).map(
    (date) => byDate.get(date) ?? { date, login_success: 0, login_failure: 0, suspicious: 0 },
  )
}

function buildTrendOption(points: SecurityTrendPoint[], hidden: ReadonlySet<string>): ChartOption {
  const names = TREND_SERIES.map((item) => item.name)
  const showSymbol = points.length <= 14
  return {
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#3b4557', width: 1 } },
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        const point = points[rows[0]?.dataIndex ?? 0]
        if (!point) return ''
        const items = TREND_SERIES.map((series) => ({
          label: series.name,
          value: point[series.key],
          color: series.color,
        }))
        const total = items.reduce((sum, item) => sum + item.value, 0)
        return tooltipHtml(
          dayLabelLong(point.date),
          items.map((item) => ({ label: item.label, value: `${item.value} 条`, color: item.color })),
          `${total} 条`,
        )
      },
    },
    legend: { show: false, selected: Object.fromEntries(names.map((name) => [name, !hidden.has(name)])) },
    xAxis: categoryAxis(points.map((point) => dayLabel(point.date))),
    yAxis: valueAxis((value) => compactCount(value)),
    series: TREND_SERIES.map((series) => ({
      name: series.name,
      type: 'line' as const,
      stack: 'events',
      smooth: false,
      symbol: 'circle' as const,
      symbolSize: 5,
      showSymbol,
      lineStyle: { width: 1.5, color: series.color },
      itemStyle: { color: series.color },
      areaStyle: { color: areaGradient(series.color) },
      emphasis: { focus: 'series' as const },
      data: points.map((point) => point[series.key]),
    })),
  }
}

function shareItems(data: SecurityOverview, mode: ShareMode) {
  if (mode === 'outcome') {
    return OUTCOME_ITEMS.map((item) => ({
      name: item.name,
      value: data.outcome_counts[item.key] ?? 0,
      color: item.color,
    })).filter((item) => item.value > 0)
  }
  return data.client_counts
    .map((item, index) => ({ name: item.name, value: item.count, color: paletteColor(index) }))
    .filter((item) => item.value > 0)
}

function buildShareOption(items: { name: string; value: number; color: string }[]): ChartOption {
  return {
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'item',
      formatter: (params: unknown) => {
        const row = (Array.isArray(params) ? params[0] : params) as ChartTooltipParam & { percent?: number }
        if (!row) return ''
        return tooltipHtml(String(row.name ?? ''), [
          { label: '数量', value: `${Number(row.value ?? 0)} 条`, color: row.color },
          { label: '占比', value: formatPercent((row.percent ?? 0) / 100, 1) },
        ])
      },
    },
    legend: {
      bottom: 0,
      left: 'center',
      type: 'scroll',
      icon: 'roundRect',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 12,
      textStyle: { color: CHART_TEXT, fontSize: 11 },
      pageTextStyle: { color: CHART_TEXT_SOFT },
    },
    series: [
      {
        type: 'pie' as const,
        radius: ['52%', '74%'],
        center: ['50%', '46%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#12161d', borderWidth: 2, borderRadius: 4 },
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 4 },
        data: items.map((item) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: item.color },
        })),
      },
    ],
  }
}

function TrendChart({ data }: { data: SecurityOverview }) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const points = useMemo(() => expandTrend(data), [data])
  const option = useMemo(() => buildTrendOption(points, hidden), [points, hidden])
  const total = points.reduce((sum, point) => sum + point.login_success + point.login_failure + point.suspicious, 0)
  const table = useMemo<ChartTable>(
    () => ({
      head: ['日期', '登录成功', '登录失败', '异常请求'],
      rows: points.map((point) => [point.date, point.login_success, point.login_failure, point.suspicious]),
    }),
    [points],
  )
  const items = TREND_SERIES.map((series) => ({ name: series.name, color: series.color }))

  return (
    <ChartCard
      title="事件趋势"
      description={`近 ${Math.max(points.length - 1, 0)} 天 · 共 ${total} 条`}
      table={table}
    >
      {total === 0 ? (
        <ChartEmpty title="暂无事件" description="统计区间内没有安全事件。" />
      ) : (
        <>
          <EChart option={option} ariaLabel={`安全事件趋势，区间内共 ${total} 条`} className="h-[240px] lg:h-[280px]" />
          <SeriesLegend
            items={items}
            hidden={hidden}
            onToggle={(name) =>
              setHidden((prev) => {
                const next = new Set(prev)
                if (next.has(name)) next.delete(name)
                else next.add(name)
                return next
              })
            }
            onShowAll={() => setHidden(new Set())}
          />
        </>
      )}
    </ChartCard>
  )
}

function ShareChart({ data }: { data: SecurityOverview }) {
  const [mode, setMode] = useState<ShareMode>('outcome')
  const items = useMemo(() => shareItems(data, mode), [data, mode])
  const option = useMemo(() => buildShareOption(items), [items])
  const total = items.reduce((sum, item) => sum + item.value, 0)
  const table = useMemo<ChartTable>(
    () => ({
      head: ['分类', '数量', '占比'],
      rows: items.map((item) => [item.name, item.value, formatPercent(item.value / (total || 1), 1)]),
    }),
    [items, total],
  )

  return (
    <ChartCard
      title="构成分布"
      description={mode === 'outcome' ? '按后端响应结果' : '按客户端标识'}
      actions={
        <ChartTabs
          label="构成分布口径"
          value={mode}
          onChange={setMode}
          options={[
            { value: 'outcome', label: '响应结果' },
            { value: 'client', label: '客户端' },
          ]}
        />
      }
      table={table}
    >
      {items.length === 0 ? (
        <ChartEmpty title="暂无数据" description="统计区间内没有可统计的事件。" />
      ) : (
        <div className="relative">
          <EChart
            option={option}
            ariaLabel={`${mode === 'outcome' ? '响应结果' : '客户端'}构成分布，共 ${total} 条`}
            className="h-[240px] lg:h-[280px]"
          />
          <div className="pointer-events-none absolute inset-x-0 top-[46%] -translate-y-1/2 text-center">
            <div className="font-mono text-xl font-semibold tabular-nums text-paper">{compactCount(total)}</div>
            <div className="text-xs text-mist-soft">事件</div>
          </div>
        </div>
      )}
    </ChartCard>
  )
}

export function SecurityCharts({ data }: { data: SecurityOverview }) {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <TrendChart data={data} />
      <ShareChart data={data} />
    </div>
  )
}
