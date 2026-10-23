import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { Card } from './ui'
import type { DashboardStats } from '../lib/api'
import { formatPercent } from '../lib/chart-theme'
import { cn, formatCount, formatDuration, formatTokenCount } from '../lib/utils'

type Tone = 'good' | 'bad' | 'neutral'
type Direction = 'up' | 'down' | 'flat'

const TONE_CLASS: Record<Tone, string> = {
  good: 'text-ok',
  bad: 'text-danger',
  neutral: 'text-mist-soft',
}

type Delta = { text: string; tone: Tone; direction: Direction }

/**
 * 环比：上期为 0 时不编造百分比，直接说明「上期无数据」。
 * invert 用于耗时这类「越低越好」的指标，避免下降被标成红色；
 * 箭头方向始终跟随数值变化，颜色才表达好坏。
 */
function percentDelta(current: number, previous: number, invert = false): Delta | null {
  if (previous === 0) return current === 0 ? null : { text: '上期无数据', tone: 'neutral', direction: 'flat' }
  const change = (current - previous) / previous
  if (!Number.isFinite(change)) return null
  const tone: Tone = change === 0 ? 'neutral' : (change > 0) !== invert ? 'good' : 'bad'
  return {
    text: `较上期 ${change > 0 ? '+' : ''}${(change * 100).toFixed(1)}%`,
    tone,
    direction: change > 0 ? 'up' : change < 0 ? 'down' : 'flat',
  }
}

function pointDelta(current: number, previous: number): Delta | null {
  if (previous === 0 && current === 0) return null
  if (previous === 0) return { text: '上期无数据', tone: 'neutral', direction: 'flat' }
  const diff = (current - previous) * 100
  return {
    text: `较上期 ${diff > 0 ? '+' : ''}${diff.toFixed(1)}pp`,
    tone: diff === 0 ? 'neutral' : diff > 0 ? 'good' : 'bad',
    direction: diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat',
  }
}

function DeltaText({ delta }: { delta: Delta | null }) {
  if (!delta) return <span className="text-mist-soft">较上期 —</span>
  const Icon = delta.direction === 'up' ? ArrowUpRight : delta.direction === 'down' ? ArrowDownRight : Minus
  return (
    <span className={cn('inline-flex items-center gap-0.5', TONE_CLASS[delta.tone])}>
      <Icon size={12} aria-hidden="true" />
      {delta.text}
    </span>
  )
}

function RangeStat({
  label,
  value,
  hint,
  delta,
  loading,
}: {
  label: string
  value: string
  hint?: string
  delta?: Delta | null
  loading?: boolean
}) {
  if (loading) {
    return (
      <Card className="min-w-0">
        <div className="h-3 w-16 animate-pulse rounded bg-white/5" />
        <div className="mt-3 h-7 w-24 animate-pulse rounded bg-white/5" />
        <div className="mt-2 h-3 w-28 animate-pulse rounded bg-white/5" />
      </Card>
    )
  }
  return (
    <Card className="min-w-0">
      <div className="text-xs uppercase tracking-[0.16em] text-mist">{label}</div>
      <div className="mt-2 truncate font-mono text-xl tabular-nums text-paper lg:text-2xl" title={value}>
        {value}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        {delta !== undefined ? <DeltaText delta={delta} /> : null}
        {hint ? <span className="text-mist-soft">{hint}</span> : null}
      </div>
    </Card>
  )
}

/** 区间概览：近 N 天的请求、Token、成功率、耗时，并给出与上一个等长区间的对比。 */
export function DashboardSummary({ data }: { data?: DashboardStats }) {
  const summary = data?.summary
  const previous = data?.previous
  const days = data?.days ?? 7

  return (
    <section className="space-y-3" aria-label="区间概览">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-xs uppercase tracking-[0.16em] text-mist">区间概览</h2>
        <span className="font-mono text-xs tabular-nums text-mist-soft">
          {data ? `近 ${data.days} 天 · ${data.start_date.slice(5)} ~ ${data.end_date.slice(5)}` : `近 ${days} 天`}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <RangeStat
          label="调用"
          value={summary ? formatCount(summary.requests) : '—'}
          hint={summary ? `日均 ${formatCount(Math.round(summary.requests / days))} 次` : undefined}
          delta={summary && previous ? percentDelta(summary.requests, previous.requests) : null}
          loading={!summary}
        />
        <RangeStat
          label="Token"
          value={summary ? formatTokenCount(summary.tokens) : '—'}
          hint={summary ? `输入 ${formatTokenCount(summary.prompt_tokens)} · 输出 ${formatTokenCount(summary.completion_tokens)}` : undefined}
          delta={summary && previous ? percentDelta(summary.tokens, previous.tokens) : null}
          loading={!summary}
        />
        <RangeStat
          label="成功率"
          // 没有请求时成功率为 0 是假数据，直接显示占位符
          value={summary ? (summary.requests ? formatPercent(summary.success_rate, 1) : '—') : '—'}
          hint={summary ? `失败 ${formatCount(summary.failures)} 次` : undefined}
          delta={summary && previous ? pointDelta(summary.success_rate, previous.success_rate) : null}
          loading={!summary}
        />
        <RangeStat
          label="平均耗时"
          value={summary ? formatDuration(summary.avg_latency_ms) : '—'}
          hint={summary ? `P95 ${formatDuration(summary.p95_latency_ms)}` : undefined}
          delta={summary && previous ? percentDelta(summary.avg_latency_ms ?? 0, previous.avg_latency_ms ?? 0, true) : null}
          loading={!summary}
        />
      </div>
    </section>
  )
}
