import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Activity } from 'lucide-react'
import { useState } from 'react'
import { api } from '../lib/api'
import { cn, errorMessage } from '../lib/utils'
import { Button, Card } from './ui'

type Period = '1h' | '24h'
const time = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const tones = { received: 'bg-ok/80', missing: 'bg-warn/55', unknown: 'bg-mist/20' }
const periods: { value: Period; label: string }[] = [
  { value: '1h', label: '最近 1 小时' },
  { value: '24h', label: '最近 24 小时' },
]

export function ExternalMcpHeartbeat({ serviceId }: { serviceId: string }) {
  const [period, setPeriod] = useState<Period>('1h')
  const [point, setPoint] = useState<number | null>(null)
  const query = useQuery({
    queryKey: ['mcp-external', serviceId, 'heartbeats', period],
    queryFn: () => api.externalMcpHeartbeats(serviceId, period),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  })
  const history = query.data
  const bucket = point ?? (history ? history.counts.length - 1 : 0)

  return <Card className="min-w-0 space-y-3">
    <div className="flex flex-wrap items-start justify-between gap-3" aria-busy={query.isPlaceholderData}>
      <div>
        <h3 className="flex items-center gap-2 text-base font-semibold leading-6"><Activity size={16} aria-hidden="true" />心跳历史</h3>
        <p className="mt-0.5 text-sm leading-6 text-mist">按时间汇总所有实例的注册和续租，每 15 秒刷新。</p>
      </div>
      <div role="group" aria-label="心跳历史范围" className="inline-flex flex-wrap gap-1 rounded-full bg-elevate/55 p-1">
        {periods.map(({ value, label }) => <Button key={value} type="button" variant="ghost" size="sm"
          aria-pressed={period === value}
          className={cn('min-h-9 rounded-full px-3 text-xs active:scale-100', period === value ? 'bg-signal/15 text-signal hover:bg-signal/20' : 'text-mist hover:text-paper')}
          onClick={() => { if (period !== value) { setPeriod(value); setPoint(null) } }}>{label}</Button>)}
      </div>
    </div>
    {query.isPending ? <div role="status" aria-label="加载心跳历史" className="h-14 rounded bg-elevate motion-safe:animate-pulse" /> : null}
    {query.isError ? <p role="alert" className="text-sm text-danger">{errorMessage(query.error, '加载心跳历史失败')}</p> : null}
    {history ? <>
      <div className="space-y-2 pt-1">
        <div role="img" aria-label={`心跳时间轴，每格 ${history.bucket_seconds / 60} 分钟。`}
          className="grid h-9 min-w-0 items-center gap-px rounded-lg bg-elevate/30 px-2 sm:gap-0.5"
          style={{ gridTemplateColumns: `repeat(${history.counts.length}, minmax(0, 1fr))` }}>
          {history.counts.map((value, index) => <div key={index} aria-hidden="true" onPointerEnter={() => setPoint(index)}
            className={cn('h-4 rounded-full motion-safe:transition-[filter] sm:h-5', index === bucket && 'brightness-150', tones[value === null ? 'unknown' : value > 0 ? 'received' : 'missing'])} />)}
        </div>
        <div className="flex justify-between gap-2 text-xs tabular-nums text-mist">
          <span>{time(history.start)}</span><span>{time(history.start + (history.counts.length / 2) * history.bucket_seconds)}</span><span>现在</span>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist">
        {([['received', '收到心跳'], ['missing', '未收到心跳'], ['unknown', '尚未开始记录']] as const).map(([tone, label]) => (
          <span key={tone} className="inline-flex items-center gap-1.5"><span aria-hidden="true" className={cn('h-2 w-2 rounded-sm', tones[tone])} />{label}</span>
        ))}
      </div>
    </> : null}
  </Card>
}
