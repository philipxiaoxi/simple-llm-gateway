import { useCallback, useMemo, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { Button } from './ui'
import { ChartCard, ChartEmpty, ChartTabs, type ChartTable } from './charts/chart-card'
import { EChart } from './charts/echart'
import { SeriesLegend } from './charts/chart-legend'
import {
  CHART_FONT_MONO,
  CHART_PALETTE,
  CHART_TEXT,
  CHART_TOOLTIP_BASE,
  areaGradient,
  categoryAxis,
  compactCount,
  dayLabel,
  dayLabelLong,
  formatPercent,
  tooltipHtml,
  valueAxis,
  withAlpha,
  type ChartOption,
  type ChartTooltipParam,
} from '../lib/chart-theme'
import type { DashboardStats } from '../lib/api'
import { useMediaQuery } from '../lib/useMediaQuery'
import { formatCount, formatTokenCount } from '../lib/utils'

function paletteColor(index: number) {
  return CHART_PALETTE[((index % CHART_PALETTE.length) + CHART_PALETTE.length) % CHART_PALETTE.length]
}

function peakDay(data: DashboardStats) {
  let peak = data.daily[0]
  for (const day of data.daily) {
    if (day.requests > (peak?.requests ?? 0)) peak = day
  }
  return peak
}

function buildCallsOption(data: DashboardStats, hidden: ReadonlySet<string>): ChartOption {
  const names = data.models.map((item) => item.model)
  const labels = data.daily.map((day) => dayLabel(day.date))
  return {
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        const day = data.daily[rows[0]?.dataIndex ?? 0]
        if (!day) return ''
        const items = rows
          .map((row) => ({
            label: String(row.seriesName ?? ''),
            value: Number(row.value ?? 0),
            color: row.color,
          }))
          .filter((item) => item.value > 0)
          .sort((a, b) => b.value - a.value)
        return tooltipHtml(
          `${dayLabelLong(day.date)} 调用`,
          items.map((item) => ({ label: item.label, value: `${formatCount(item.value)} 次`, color: item.color })),
          `${formatCount(day.requests)} 次`,
        )
      },
    },
    // 图例由 DOM 版 SeriesLegend 渲染，这里只回传选中状态；隐藏时 ECharts 仍会重算堆叠与坐标轴
    legend: { show: false, selected: Object.fromEntries(names.map((name) => [name, !hidden.has(name)])) },
    xAxis: categoryAxis(labels),
    yAxis: valueAxis((value) => compactCount(value)),
    series: names.map((name, index) => ({
      name,
      type: 'bar' as const,
      stack: 'calls',
      barMaxWidth: 28,
      itemStyle: {
        color: paletteColor(index),
        // 只有栈顶系列给圆角，堆叠看起来才像一根柱子而不是拼图
        borderRadius: index === names.length - 1 ? [3, 3, 0, 0] : 0,
      },
      emphasis: { focus: 'series' as const },
      data: data.daily.map((day) => day.model_counts[name] ?? 0),
    })),
  }
}

function buildTokensOption(data: DashboardStats, hidden: ReadonlySet<string>): ChartOption {
  const labels = data.daily.map((day) => dayLabel(day.date))
  const showSymbol = data.daily.length <= 14
  const series = (name: string, key: 'prompt_tokens' | 'completion_tokens', color: string) => ({
    name,
    type: 'line' as const,
    stack: 'tokens',
    smooth: false,
    symbol: 'circle' as const,
    symbolSize: 5,
    showSymbol,
    lineStyle: { width: 1.5, color },
    itemStyle: { color },
    areaStyle: { color: areaGradient(color) },
    emphasis: { focus: 'series' as const },
    data: data.daily.map((day) => day[key]),
  })
  return {
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#3b4557', width: 1 } },
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        const day = data.daily[rows[0]?.dataIndex ?? 0]
        if (!day) return ''
        const items = [
          { label: '输入 Token', value: day.prompt_tokens, color: paletteColor(1) },
          { label: '输出 Token', value: day.completion_tokens, color: paletteColor(0) },
        ]
        return tooltipHtml(
          `${dayLabelLong(day.date)} Token`,
          items.map((item) => ({ label: item.label, value: formatTokenCount(item.value), color: item.color })),
          formatTokenCount(day.tokens),
        )
      },
    },
    legend: {
      show: false,
      selected: {
        '输入 Token': !hidden.has('输入 Token'),
        '输出 Token': !hidden.has('输出 Token'),
      },
    },
    xAxis: categoryAxis(labels),
    yAxis: valueAxis((value) => compactCount(value)),
    series: [series('输入 Token', 'prompt_tokens', paletteColor(1)), series('输出 Token', 'completion_tokens', paletteColor(0))],
  }
}

type ShareMetric = 'requests' | 'tokens'

function buildShareOption(data: DashboardStats, metric: ShareMetric, compact: boolean): ChartOption {
  const colorIndex = new Map(data.models.map((item, index) => [item.model, index]))
  const sorted = [...data.models].sort((a, b) => b[metric] - a[metric] || a.model.localeCompare(b.model))
  // ECharts 的类目轴从下往上画，反转后排行榜第一位才在顶部
  const items = [...sorted].reverse()
  const maxValue = Math.max(1, ...items.map((item) => item[metric]))
  return {
    grid: { left: 4, right: 60, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'item',
      formatter: (params: unknown) => {
        const param = (Array.isArray(params) ? params[0] : params) as ChartTooltipParam
        const item = items[param.dataIndex ?? 0]
        if (!item) return ''
        return tooltipHtml(item.model, [
          { label: '调用次数', value: `${formatCount(item.requests)} 次`, color: paletteColor(colorIndex.get(item.model) ?? 0) },
          { label: 'Token', value: formatTokenCount(item.tokens) },
          { label: '调用占比', value: formatPercent(item.request_share, 1) },
        ])
      },
    },
    xAxis: { type: 'value', show: false, max: maxValue * 1.18 },
    yAxis: {
      type: 'category',
      data: items.map((item) => item.model),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: CHART_TEXT,
        fontSize: 11,
        width: compact ? 96 : 150,
        overflow: 'truncate',
        margin: 10,
      },
    },
    series: [
      {
        type: 'bar' as const,
        barMaxWidth: 14,
        data: items.map((item) => ({
          value: item[metric],
          itemStyle: {
            color: paletteColor(colorIndex.get(item.model) ?? 0),
            borderRadius: [0, 3, 3, 0],
          },
        })),
        label: {
          show: true,
          position: 'right' as const,
          color: CHART_TEXT,
          fontSize: 11,
          fontFamily: CHART_FONT_MONO,
          formatter: (params: unknown) => {
            const value = Number((params as { value?: unknown }).value ?? 0)
            return metric === 'tokens' ? formatTokenCount(value) : formatCount(value)
          },
        },
      },
    ],
  }
}

function buildHourlyOption(data: DashboardStats): ChartOption {
  const labels = data.hourly.map((item) => String(item.hour).padStart(2, '0'))
  const values = data.hourly.map((item) => item.requests)
  const max = Math.max(0, ...values)
  const peakIndex = max > 0 ? values.indexOf(max) : -1
  return {
    grid: { left: 8, right: 12, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      ...CHART_TOOLTIP_BASE,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const rows = (Array.isArray(params) ? params : [params]) as ChartTooltipParam[]
        const index = rows[0]?.dataIndex ?? 0
        const value = values[index] ?? 0
        return tooltipHtml(`${labels[index]}:00 时段`, [
          { label: '调用次数', value: `${formatCount(value)} 次`, color: index === peakIndex ? paletteColor(0) : withAlpha(paletteColor(1), 0.8) },
          { label: '区间占比', value: formatPercent(value / Math.max(1, data.summary.requests), 1) },
        ])
      },
    },
    xAxis: { ...categoryAxis(labels), axisLabel: { ...categoryAxis(labels).axisLabel, interval: 3 } },
    yAxis: valueAxis((value) => compactCount(value)),
    series: [
      {
        type: 'bar' as const,
        barMaxWidth: 16,
        data: values.map((value, index) => ({
          value,
          itemStyle: {
            color: index === peakIndex ? paletteColor(0) : withAlpha(paletteColor(1), 0.5),
            borderRadius: [2, 2, 0, 0],
          },
        })),
      },
    ],
  }
}

function DailyCallsChart({ data }: { data: DashboardStats }) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>())
  const option = useMemo(() => buildCallsOption(data, hidden), [data, hidden])
  const legendItems = useMemo(
    () => data.models.map((item, index) => ({ name: item.model, color: paletteColor(index) })),
    [data],
  )
  const toggle = useCallback((name: string) => {
    setHidden((previous) => {
      const next = new Set(previous)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])
  const peak = useMemo(() => peakDay(data), [data])
  const table = useMemo<ChartTable>(() => {
    const names = data.models.map((item) => item.model)
    return {
      head: ['日期', ...names, '合计'],
      rows: data.daily.map((day) => [
        dayLabel(day.date),
        ...names.map((name) => day.model_counts[name] ?? 0),
        day.requests,
      ]),
    }
  }, [data])
  const ariaLabel = `近 ${data.days} 天每日调用次数堆叠柱状图，合计 ${formatCount(data.summary.requests)} 次${
    peak ? `，峰值 ${dayLabelLong(peak.date)} 共 ${formatCount(peak.requests)} 次` : ''
  }，按模型拆分：${data.models.map((item) => `${item.model} ${item.requests} 次`).join('，')}`

  return (
    <ChartCard
      title="每日调用次数"
      description={`合计 ${formatCount(data.summary.requests)} 次${
        peak ? ` · 峰值 ${dayLabel(peak.date)} 共 ${formatCount(peak.requests)} 次` : ''
      } · 按模型堆叠`}
      table={table}
    >
      <EChart option={option} ariaLabel={ariaLabel} className="h-[248px] lg:h-[280px]" />
      <SeriesLegend items={legendItems} hidden={hidden} onToggle={toggle} onShowAll={() => setHidden(new Set())} />
    </ChartCard>
  )
}

function DailyTokensChart({ data }: { data: DashboardStats }) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>())
  const option = useMemo(() => buildTokensOption(data, hidden), [data, hidden])
  const legendItems = useMemo(
    () => [
      { name: '输入 Token', color: paletteColor(1) },
      { name: '输出 Token', color: paletteColor(0) },
    ],
    [],
  )
  const toggle = useCallback((name: string) => {
    setHidden((previous) => {
      const next = new Set(previous)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])
  const table = useMemo<ChartTable>(
    () => ({
      head: ['日期', '输入', '输出', '合计'],
      rows: data.daily.map((day) => [dayLabel(day.date), day.prompt_tokens, day.completion_tokens, day.tokens]),
    }),
    [data],
  )
  const promptShare = data.summary.tokens ? data.summary.prompt_tokens / data.summary.tokens : 0
  const ariaLabel = `近 ${data.days} 天每日 Token 消耗堆叠面积图，合计 ${formatTokenCount(data.summary.tokens)}，其中输入 ${formatTokenCount(data.summary.prompt_tokens)}、输出 ${formatTokenCount(data.summary.completion_tokens)}`

  return (
    <ChartCard
      title="每日 Token 消耗"
      description={`合计 ${formatTokenCount(data.summary.tokens)} · 输入 ${formatPercent(promptShare, 0)} / 输出 ${formatPercent(1 - promptShare, 0)}`}
      table={table}
    >
      <EChart option={option} ariaLabel={ariaLabel} className="h-[248px] lg:h-[280px]" />
      <SeriesLegend items={legendItems} hidden={hidden} onToggle={toggle} onShowAll={() => setHidden(new Set())} />
    </ChartCard>
  )
}

function ModelShareChart({ data, height }: { data: DashboardStats; height: number }) {
  const [metric, setMetric] = useState<ShareMetric>('requests')
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const option = useMemo(() => buildShareOption(data, metric, !isDesktop), [data, metric, isDesktop])
  const table = useMemo<ChartTable>(() => {
    const sorted = [...data.models].sort((a, b) => b.requests - a.requests)
    return {
      head: ['模型', '调用次数', '占比', 'Token'],
      rows: sorted.map((item) => [item.model, item.requests, formatPercent(item.request_share, 1), item.tokens]),
    }
  }, [data])
  const top = [...data.models].sort((a, b) => b.requests - a.requests)[0]
  const ariaLabel = `近 ${data.days} 天模型调用占比排行榜，共 ${data.models.length} 个模型，最多的是 ${top?.model ?? '无'}，${top ? formatCount(top.requests) : 0} 次，占 ${formatPercent(top?.request_share ?? 0, 1)}`

  return (
    <ChartCard
      title="模型占比"
      description={`近 ${data.days} 天 · 最多 ${top?.model ?? '—'}`}
      actions={
        <ChartTabs
          label="模型占比口径"
          value={metric}
          onChange={setMetric}
          options={[
            { value: 'requests', label: '次数' },
            { value: 'tokens', label: 'Token' },
          ]}
        />
      }
      table={table}
    >
      <EChart option={option} ariaLabel={ariaLabel} style={{ height }} />
    </ChartCard>
  )
}

function HourlyChart({ data, height }: { data: DashboardStats; height: number }) {
  const option = useMemo(() => buildHourlyOption(data), [data])
  const peak = useMemo(() => {
    let best = { hour: 0, requests: 0 }
    for (const item of data.hourly) if (item.requests > best.requests) best = item
    return best
  }, [data])
  const table = useMemo<ChartTable>(
    () => ({
      head: ['时段', '调用次数'],
      rows: data.hourly.map((item) => [`${String(item.hour).padStart(2, '0')}:00`, item.requests]),
    }),
    [data],
  )
  const ariaLabel = `近 ${data.days} 天按小时调用分布，最忙的是 ${String(peak.hour).padStart(2, '0')}:00 时段，共 ${formatCount(peak.requests)} 次`

  return (
    <ChartCard
      title="时段分布"
      description={`上海时区 · 峰值 ${String(peak.hour).padStart(2, '0')}:00 共 ${formatCount(peak.requests)} 次`}
      table={table}
    >
      <EChart option={option} ariaLabel={ariaLabel} style={{ height }} />
    </ChartCard>
  )
}

function AccountUsageChart({ data }: { data: DashboardStats }) {
  const table = useMemo<ChartTable>(
    () => ({
      head: ['账号', '调用次数', '占比', 'Token'],
      rows: data.accounts.map((item) => [item.account_name, item.requests, formatPercent(item.request_share, 1), item.tokens]),
    }),
    [data],
  )

  return (
    <ChartCard
      title="上游账号消耗"
      description={`近 ${data.days} 天 · 按调用次数排序，可看出流量压在哪条上游`}
      table={table}
      className="xl:col-span-2"
    >
      {data.accounts.length ? (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.accounts.map((item) => (
            <li key={item.account_name} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-sm text-paper" title={item.account_name}>
                  {item.account_name}
                </span>
                <span className="shrink-0 font-mono text-xs tabular-nums text-mist-strong">
                  {formatTokenCount(item.tokens)} · {formatCount(item.requests)} 次
                </span>
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/5">
                  <div
                    className="h-full rounded-full bg-info/70"
                    style={{ width: `${Math.max(2, Math.round(item.request_share * 100))}%` }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right font-mono text-[11px] tabular-nums text-mist-soft">
                  {formatPercent(item.request_share, 0)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-sm text-mist">区间内没有上游账号调用记录。</div>
      )}
    </ChartCard>
  )
}

/** 概览图表区：调用/Token 趋势、模型占比、时段分布、上游账号消耗。 */
export function DashboardCharts({
  data,
  onSelectDays,
}: {
  data: DashboardStats
  onSelectDays?: (days: number) => void
}) {
  if (data.summary.requests === 0) {
    return (
      <ChartCard title="调用趋势" description={`近 ${data.days} 天 · ${data.start_date} ~ ${data.end_date}`}>
        <ChartEmpty
          title="该区间还没有调用记录"
          description="换个更长的区间看看，或到「请求日志」查看历史调用。"
          action={
            data.days < 90 && onSelectDays ? (
              <Button variant="line" onClick={() => onSelectDays(90)}>
                <BarChart3 size={14} aria-hidden="true" />
                查看近 90 天
              </Button>
            ) : undefined
          }
        />
      </ChartCard>
    )
  }

  // 模型占比和时段分布同一行：按模型条数算出共同高度，两张卡底部才能对齐
  const rowHeight = Math.min(400, Math.max(220, data.models.length * 30 + 24))

  return (
    <section className="grid gap-3 xl:grid-cols-2" aria-label="调用趋势图表">
      <DailyCallsChart data={data} />
      <DailyTokensChart data={data} />
      <ModelShareChart data={data} height={rowHeight} />
      <HourlyChart data={data} height={rowHeight} />
      <AccountUsageChart data={data} />
    </section>
  )
}
