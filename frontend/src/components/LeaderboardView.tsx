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

const COL_NARROW = 'w-16 min-w-16 whitespace-nowrap px-4 py-3'
const COL = 'min-w-36 whitespace-nowrap px-4 py-3'

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

function matchTitle(match: LeaderboardLocalMatch) {
  if (match.kind === 'agent') {
    const agent = match.agent_id || '网关代理'
    const route = match.account_name || match.agent_route_id || ''
    return route ? `${agent} / ${route}` : agent
  }
  return match.account_name
}

function LocalCoverage({ entry }: { entry: LeaderboardEntry }) {
  const matches = entry.local_matches ?? []
  if (!entry.local_covered || !matches.length) {
    return <Badge tone="mist">未覆盖</Badge>
  }
  return (
    <div className="flex flex-col gap-1.5">
      {matches.map((match, index) => (
        <div key={`${match.kind}-${match.account_id}-${match.matched_model}-${index}`}>
          <Badge tone={match.kind === 'agent' ? 'info' : 'ok'} title={match.matched_model}>
            {match.kind === 'agent' ? '网关代理' : '上游账号'} {matchTitle(match)}
          </Badge>
          <div className="mt-0.5 font-mono text-[11px] text-mist">{match.matched_model}</div>
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
  const items = data?.items ?? []
  const filtered = useMemo(() => {
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
  }, [items, search])

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">条目</div>
          <div className="mt-3 font-mono text-3xl text-signal">{data ? data.total : '—'}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">缓存时间</div>
          <div className="mt-3 text-lg text-paper">{data?.fetched_at ? formatTime(data.fetched_at) : '尚未拉取'}</div>
        </Card>
      </div>

      {data?.error_message ? (
        <div className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">{data.error_message}</div>
      ) : null}

      <Card>
        <Field label="搜索">
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索模型 / 厂商 / 本站覆盖" />
        </Field>
      </Card>

      <Card className="overflow-hidden p-0">
        <div className="hidden overflow-x-auto md:block">
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
                  <td className="min-w-36 px-4 py-3">
                    <div className="whitespace-nowrap font-medium">{item.name}</div>
                    <div className="whitespace-nowrap text-xs text-mist">{item.provider}</div>
                  </td>
                  <td className={`${COL} font-mono text-mist`}>{formatReleaseDate(item.released_at)}</td>
                  <td className={`${COL_NARROW} font-mono text-signal`}>{formatScore(item.score)}</td>
                  <td className={COL}>
                    <Badge tone={confidenceTone[item.confidence || ''] || 'mist'}>
                      {item.confidence || '—'} · {item.coverage == null ? '—' : `${Math.round(item.coverage * 100)}%`}
                    </Badge>
                  </td>
                  <td className="min-w-36 px-4 py-3">
                    <LocalCoverage entry={item} />
                  </td>
                  <td className={`${COL} font-mono text-mist`}>{formatPrice(item.input_price_per_million_cny)}</td>
                  <td className={`${COL} font-mono text-mist`}>{formatPrice(item.output_price_per_million_cny)}</td>
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
        <div className="grid gap-3 p-3 md:hidden">
          {filtered.map((item) => (
            <div key={item.slug} className="border border-line px-3 py-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-mist">{String(item.rank ?? '—').padStart(2, '0')}</span>
                <span className="font-mono text-signal">{formatScore(item.score)}</span>
              </div>
              <div className="mt-1 font-medium">{item.name}</div>
              <div className="text-xs text-mist">{item.provider}</div>
              <div className="mt-2 text-xs text-mist">上线 {formatReleaseDate(item.released_at)}</div>
              <div className="mt-2">
                <LocalCoverage entry={item} />
              </div>
            </div>
          ))}
          {!isLoading && !filtered.length ? (
            <div className="px-3 py-10 text-center text-sm text-mist">
              {items.length ? '没有匹配的模型' : emptyText}
            </div>
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
