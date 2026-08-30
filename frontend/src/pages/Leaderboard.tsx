import { useQuery } from '@tanstack/react-query'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { LeaderboardView } from '../components/LeaderboardView'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'

export function LeaderboardPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['leaderboard'],
    queryFn: () => api.leaderboard(),
  })

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">AIHOT CACHE</div>
            <h1 className="text-2xl font-semibold">模型榜</h1>
            <p className="mt-1 text-sm text-mist">只读缓存。后台每 12 小时拉取 AIHOT 总榜前 30 名。</p>
          </div>
          <Button
            type="button"
            variant="line"
            className="shrink-0 lg:hidden"
            disabled={isFetching}
            onClick={() => void refetch()}
          >
            <RefreshCw size={16} className={isFetching ? 'animate-spin' : undefined} />
            刷新
          </Button>
        </div>
        <div className="grid grid-cols-2 items-end gap-2 lg:flex lg:shrink-0 lg:flex-nowrap">
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            onClick={() => {
              void navigator.clipboard.writeText(`${window.location.origin}/share/leaderboard`).then(
                () => notifyOk('已复制公开模型榜地址'),
                () => notifyBad('复制失败'),
              )
            }}
          >
            复制公开页
          </Button>
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            onClick={() => window.open('/share/leaderboard', '_blank', 'noopener')}
          >
            打开公开页
          </Button>
          <a className="w-full lg:w-auto" href="https://aihot.virxact.com/leaderboard" target="_blank" rel="noreferrer">
            <Button type="button" variant="line" className="w-full lg:w-auto">
              <ExternalLink size={16} />
              原站
            </Button>
          </a>
          <Button
            type="button"
            variant="line"
            className="hidden lg:inline-flex"
            disabled={isFetching}
            onClick={() => void refetch()}
          >
            <RefreshCw size={16} className={isFetching ? 'animate-spin' : undefined} />
            刷新
          </Button>
        </div>
      </div>

      <LeaderboardView
        data={data}
        isLoading={isLoading}
        isError={isError}
        error={error}
        emptyText="还没有缓存。后台每 12 小时拉取，也可到定时任务页立即请求。"
        onRetry={() => void refetch()}
      />
    </div>
  )
}
