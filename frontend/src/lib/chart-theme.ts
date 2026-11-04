import type { ComposeOption } from 'echarts/core'
import type { Theme } from './theme'
import type { BarSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts'
import type { GridComponentOption, LegendComponentOption, TooltipComponentOption } from 'echarts/components'

/**
 * 图表统一配色与基础样式。
 *
 * 分类色板：前 4 个沿用品牌语义色（signal/info/warn/ok），其余是同一明度带上的扩展色，
 * 保证 12 个系列仍可区分。相邻色相刻意拉开（黄绿 → 天蓝 → 紫 → 琥珀 → 青 → 粉 …），
 * 避免堆叠柱糊成一片。浅色主题需要整体压暗，否则亮黄绿在白色底上看不清。
 */
const DARK_PALETTE = [
  '#c8f542', // signal
  '#6ec8ff', // info
  '#a78bfa', // violet
  '#f5b942', // warn
  '#2dd4bf', // teal
  '#f472b6', // pink
  '#fb923c', // orange
  '#818cf8', // indigo
  '#4ade80', // ok
  '#38bdf8', // sky
  '#e879f9', // fuchsia
  '#f25f6d', // danger
  '#94a3b8', // 其他（中性灰，表示聚合项）
] as const

const LIGHT_PALETTE = [
  '#4f7a10', // signal
  '#0369a1', // info
  '#7c3aed', // violet
  '#b45309', // warn
  '#0f766e', // teal
  '#be185d', // pink
  '#c2410c', // orange
  '#4338ca', // indigo
  '#15803d', // ok
  '#0284c7', // sky
  '#a21caf', // fuchsia
  '#dc2626', // danger
  '#64748b', // 其他（中性灰，表示聚合项）
] as const

export const CHART_FONT_MONO = '"IBM Plex Mono", ui-monospace, monospace'

function cssVar(name: string, fallback: string) {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** 读取主题令牌的实际颜色，供 canvas 图表使用（如 --color-ok） */
export function themeColor(token: string, fallback: string) {
  return cssVar(`--color-${token}`, fallback)
}

function isLight() {
  return typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
}

export type ChartTheme = {
  palette: readonly string[]
  axisLine: string
  splitLine: string
  axisPointerLine: string
  textStrong: string
  text: string
  textSoft: string
  tooltipBg: string
  tooltipBorder: string
  tooltipBase: Record<string, unknown>
}

/** ECharts 画在 canvas 上，不认 CSS var()，这里读计算值再交给它。 */
let cache: { theme: Theme; value: ChartTheme } | null = null

export function chartTheme(): ChartTheme {
  const themeKey: Theme = isLight() ? 'light' : 'dark'
  if (cache?.theme === themeKey) return cache.value
  const light = themeKey === 'light'
  const fallback = light
    ? { strong: '#0f172a', text: '#3b4657', soft: '#697486', line: '#e2e8f0', panel2: '#f6f8fc', border: '#8a97a8' }
    : { strong: '#e8edf5', text: '#a5aec0', soft: '#7d8799', line: '#2a3342', panel2: '#181d27', border: '#3b4557' }
  const axisLine = cssVar('--color-line', fallback.line)
  const tooltipBg = cssVar('--color-panel-2', fallback.panel2)
  const tooltipBorder = cssVar('--color-line-strong', fallback.border)
  const text = cssVar('--color-mist-strong', fallback.text)
  const textStrong = cssVar('--color-paper', fallback.strong)
  const textSoft = cssVar('--color-mist-soft', fallback.soft)
  const value: ChartTheme = {
    palette: light ? LIGHT_PALETTE : DARK_PALETTE,
    axisLine,
    splitLine: withAlpha(axisLine, light ? 0.55 : 0.75),
    axisPointerLine: withAlpha(axisLine, 0.9),
    textStrong,
    text,
    textSoft,
    tooltipBg,
    tooltipBorder,
    tooltipBase: {
      backgroundColor: tooltipBg,
      borderColor: tooltipBorder,
      borderWidth: 1,
      padding: 10,
      textStyle: { color: text, fontSize: 12 },
      confine: true,
      extraCssText: `border-radius:10px;box-shadow:${
        light ? '0 10px 28px rgba(15,23,42,0.12)' : '0 12px 32px rgba(0,0,0,0.45)'
      };`,
    },
  }
  cache = { theme: themeKey, value }
  return value
}

/** 堆叠柱/面积共用，ECharts 图例分页与 tooltip 都按这个类型走 */
export type ChartOption = ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
>

export type ChartTooltipParam = {
  name?: string
  seriesName?: string
  value?: unknown
  color?: string
  dataIndex?: number
  axisValueLabel?: string
  marker?: string
}

export function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 数字轴/标签用的紧凑写法：1200 → 1.2K */
export function compactCount(value: number | null | undefined) {
  if (value == null) return '—'
  if (Math.abs(value) < 1_000) return String(value)
  if (Math.abs(value) < 1_000_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`
  return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
}

/** 日期轴标签：2026-08-29 → 08-29 */
export function dayLabel(date: string) {
  return date.length >= 10 ? date.slice(5) : date
}

/** 日期完整展示：2026-08-29 → 8 月 29 日 */
export function dayLabelLong(date: string) {
  const [, month, day] = date.split('-')
  if (!month || !day) return date
  return `${Number(month)} 月 ${Number(day)} 日`
}

export function formatPercent(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function tooltipFrame(title: string, body: string, footer?: string) {
  const theme = chartTheme()
  const footerHtml = footer
    ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid ${theme.axisLine};display:flex;justify-content:space-between;gap:16px;color:${theme.text};font-size:12px"><span>合计</span><span style="color:${theme.textStrong};font-family:${CHART_FONT_MONO}">${footer}</span></div>`
    : ''
  return (
    `<div style="min-width:150px;font-size:12px;color:${theme.text}">` +
    `<div style="margin-bottom:6px;font-weight:500;color:${theme.textStrong}">${escapeHtml(title)}</div>` +
    body +
    footerHtml +
    `</div>`
  )
}

export type TooltipRow = { label: string; value: string; color?: string }

export function tooltipHtml(title: string, rows: TooltipRow[], footer?: string) {
  const theme = chartTheme()
  const body = rows
    .map(
      (row) =>
        `<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;line-height:20px">` +
        `<span style="display:inline-flex;align-items:center;gap:6px;min-width:0">` +
        (row.color
          ? `<i style="width:8px;height:8px;border-radius:2px;flex:none;background:${row.color};display:inline-block"></i>`
          : '') +
        `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px">${escapeHtml(row.label)}</span></span>` +
        `<span style="flex:none;color:${theme.textStrong};font-family:${CHART_FONT_MONO};font-variant-numeric:tabular-nums">${escapeHtml(row.value)}</span>` +
        `</div>`,
    )
    .join('')
  return tooltipFrame(title, body, footer)
}

export function categoryAxis(data: string[]) {
  const theme = chartTheme()
  return {
    type: 'category' as const,
    data,
    axisLine: { lineStyle: { color: theme.axisLine } },
    axisTick: { show: false },
    axisLabel: {
      color: theme.textSoft,
      fontSize: 11,
      fontFamily: CHART_FONT_MONO,
      hideOverlap: true,
      margin: 10,
    },
  }
}

export function valueAxis(formatter?: (value: number) => string) {
  const theme = chartTheme()
  return {
    type: 'value' as const,
    minInterval: 1,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: theme.textSoft,
      fontSize: 11,
      fontFamily: CHART_FONT_MONO,
      formatter: formatter ?? compactCount,
    },
    splitLine: { lineStyle: { color: theme.splitLine } },
  }
}

/** 面积渐变：顶部略实、底部透明，深色底上不压住网格线 */
export function areaGradient(color: string, from = 0.28, to = 0.02) {
  return {
    type: 'linear' as const,
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: withAlpha(color, from) },
      { offset: 1, color: withAlpha(color, to) },
    ],
  }
}

export function withAlpha(hex: string, alpha: number) {
  const normalized = hex.replace('#', '')
  const value = normalized.length === 3
    ? normalized
        .split('')
        .map((char) => char + char)
        .join('')
    : normalized
  const r = Number.parseInt(value.slice(0, 2), 16)
  const g = Number.parseInt(value.slice(2, 4), 16)
  const b = Number.parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
