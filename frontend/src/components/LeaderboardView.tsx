import { Trophy } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Badge, Button, Card, Field, Input } from './ui'
import type { Leaderboard, LeaderboardEntry, LeaderboardLocalMatch } from '../lib/api'
import { errorMessage, formatTime } from '../lib/utils'

const confidenceTone: Record<string, 'ok' | 'warn' | 'mist'> = {
  HIGH: 'ok',
  MEDIUM: 'warn',
  LOW: 'mist',
}

const COL_NARROW = 'w-16 min-w-16 whitespace-nowrap px-3 py-2'
const COL = 'min-w-28 whitespace-nowrap px-3 py-2'

function formatPrice(value: number | null | undefined) {
  if (value == null) return '—'
  const rounded = Number(Math.round(Number(`${value}e2`)) + 'e-2')
  return `¥${rounded.toFixed(2)}`
}

function formatScore(value: number | null | undefined) {
  if (value == null) return '—'
  return value.toFixed(1)
}

function formatReleaseDate(value: string | null | undefined) {
  if (!value) return '—'
  const formatted = formatTime(value)
  return formatted === '—' ? '—' : formatted.slice(0, 10)
}

function rankChangeText(entry: LeaderboardEntry) {
  if (entry.rank_change == null || entry.rank_change === 0) return '—'
  return entry.rank_change > 0 ? `↑${entry.rank_change}` : `↓${Math.abs(entry.rank_change)}`
}

function coverageText(entry: LeaderboardEntry) {
  const confidence = entry.confidence || '—'
  const coverage = entry.coverage == null ? '—' : `${Math.round(entry.coverage * 100)}%`
  return `${confidence} · ${coverage}`
}

function matchTitle(match: LeaderboardLocalMatch) {
  if (match.kind === 'agent') {
    const agent = match.agent_id || '网关代理'
    const route = match.account_name || match.agent_route_id || ''
    return route ? `${agent} / ${route}` : agent
  }
  return match.account_name
}

function LocalCoverage({ entry, compact = false }: { entry: LeaderboardEntry; compact?: boolean }) {
  const matches = entry.local_matches ?? []
  if (!entry.local_covered || !matches.length) {
    return <Badge tone="mist">未覆盖</Badge>
  }
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      {matches.map((match, index) => (
        <div key={`${match.kind}-${match.account_id}-${match.matched_model}-${index}`} className="min-w-0">
          <Badge tone={match.kind === 'agent' ? 'info' : 'ok'} title={match.matched_model}>
            <span className="max-w-[12rem] truncate">
              {match.kind === 'agent' ? '网关代理' : '上游账号'} {matchTitle(match)}
            </span>
          </Badge>
          {compact ? null : (
            <div className="mt-0.5 truncate font-mono text-[11px] text-mist" title={match.matched_model}>
              {match.matched_model}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function LeaderboardView({
  data,
  isLoading,
  isError,
  error,
  emptyText,
  onRetry,
}: {
  data: Leaderboard | undefined
  isLoading: boolean
  isError: boolean
  error: unknown
  emptyText: string
  onRetry: () => void
}) {
  const [search, setSearch] = useState('')
  const filtered = useMemo(() => {
    const items = data?.items ?? []
    const keyword = search.trim().toLowerCase()
    if (!keyword) return items
    return items.filter((item) => {
      const haystack = [
        item.name,
        item.slug,
        item.provider,
        item.summary,
        ...(item.local_matches ?? []).flatMap((match) => [
          match.account_name,
          match.agent_id,
          match.agent_route_id,
          match.matched_model,
          match.kind === 'agent' ? '网关代理' : '上游账号',
        ]),
      ]
      return haystack.some((value) => (value || '').toLowerCase().includes(keyword))
    })
  }, [data?.items, search])
  const items = data?.items ?? []

  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-2">
        <Card>
          <div className="text-[10px] uppercase tracking-[0.16em] text-mist lg:text-xs">条目</div>
          <div className="mt-2 truncate font-mono text-xl tabular-nums text-signal lg:mt-3 lg:text-3xl">
            {data ? data.total : '—'}
          </div>
        </Card>
        <Card>
          <div className="text-[10px] uppercase tracking-[0.16em] text-mist lg:text-xs">缓存时间</div>
          <div className="mt-2 truncate text-sm text-paper lg:mt-3 lg:text-lg">
            {data?.fetched_at ? formatTime(data.fetched_at) : '尚未拉取'}
          </div>
        </Card>
      </div>

      {data?.error_message ? (
        <div className="break-all rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn [overflow-wrap:anywhere]">
          {data.error_message}
        </div>
      ) : null}

      <Card>
        <Field label="搜索">
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索模型 / 厂商 / 本站覆盖" />
        </Field>
      </Card>

      <Card className="overflow-hidden p-0">
        <div className="hidden overflow-x-auto lg:block">
          <table className="w-max min-w-full text-left text-sm">
            <thead className="bg-panel-2 text-mist">
              <tr>
                <th className={`${COL_NARROW} font-medium`}>排名</th>
                <th className={`${COL} font-medium`}>模型</th>
                <th className={`${COL} font-medium`}>上线日期</th>
                <th className={`${COL_NARROW} font-medium`}>分数</th>
                <th className={`${COL} font-medium`}>覆盖</th>
                <th className={`${COL} font-medium`}>本站覆盖</th>
                <th className={`${COL} font-medium`}>输入价</th>
                <th className={`${COL} font-medium`}>输出价</th>
                <th className={`${COL} font-medium`}>变动</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.slug} className="border-t border-line hover:bg-white/5">
                  <td className={`${COL_NARROW} font-mono text-mist`}>{String(item.rank ?? '—').padStart(2, '0')}</td>
                  <td className="min-w-36 max-w-[220px] px-3 py-2">
                    <div className="truncate font-medium" title={item.name}>
                      {item.name}
                    </div>
                    <div className="truncate text-xs text-mist" title={item.provider}>
                      {item.provider}
                    </div>
                  </td>
                  <td className={`${COL} font-mono text-mist`}>{formatReleaseDate(item.released_at)}</td>
                  <td className={`${COL_NARROW} font-mono tabular-nums text-signal`}>{formatScore(item.score)}</td>
                  <td className={COL}>
                    <Badge tone={confidenceTone[item.confidence || ''] || 'mist'}>{coverageText(item)}</Badge>
                  </td>
                  <td className="min-w-36 max-w-[240px] px-3 py-2">
                    <LocalCoverage entry={item} />
                  </td>
                  <td className={`${COL} font-mono tabular-nums text-mist`}>{formatPrice(item.input_price_per_million_cny)}</td>
                  <td className={`${COL} font-mono tabular-nums text-mist`}>{formatPrice(item.output_price_per_million_cny)}</td>
                  <td className={`${COL} text-mist`}>{rankChangeText(item)}</td>
                </tr>
              ))}
              {!isLoading && !filtered.length ? (
                <tr>
                  <td colSpan={9} className="px-4 py-12 text-center text-sm text-mist">
                    {items.length ? '没有匹配的模型' : emptyText}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="grid gap-3 p-3 lg:hidden">
          {filtered.map((item) => (
            <div key={item.slug} className="min-w-0 space-y-3 rounded-xl border border-line px-3 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-mono text-xs text-mist">#{String(item.rank ?? '—').padStart(2, '0')}</div>
                  <div className="mt-0.5 truncate font-medium" title={item.name}>
                    {item.name}
                  </div>
                  <div className="truncate text-xs text-mist" title={item.provider}>
                    {item.provider}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <div className="font-mono text-lg tabular-nums text-signal">{formatScore(item.score)}</div>
                  <div className="text-xs text-mist">{rankChangeText(item)}</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">上线</div>
                  <div className="mt-1 truncate font-mono text-xs">{formatReleaseDate(item.released_at)}</div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">输入</div>
                  <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatPrice(item.input_price_per_million_cny)}</div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-mist">输出</div>
                  <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatPrice(item.output_price_per_million_cny)}</div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={confidenceTone[item.confidence || ''] || 'mist'}>{coverageText(item)}</Badge>
                <LocalCoverage entry={item} compact />
              </div>
            </div>
          ))}
          {!isLoading && !filtered.length ? (
            <div className="px-3 py-10 text-center text-sm text-mist">{items.length ? '没有匹配的模型' : emptyText}</div>
          ) : null}
        </div>
      </Card>

      {isError ? (
        <div className="rounded-xl border border-dashed border-danger/40 px-6 py-16 text-center">
          <Trophy className="mx-auto text-mist" />
          <div className="mt-3 text-sm text-danger">{errorMessage(error, '加载榜单失败')}</div>
          <Button type="button" variant="line" className="mt-4" onClick={onRetry}>
            重试
          </Button>
        </div>
      ) : null}
    </>
  )
}
