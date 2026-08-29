import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { LeaderboardView } from '../components/LeaderboardView'
import { Button } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'

export function LeaderboardPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['leaderboard'],
    queryFn: () => api.leaderboard(),
  })
  const refreshMutation = useMutation({
    mutationFn: () => api.leaderboard(true),
    onSuccess: async (payload) => {
      queryClient.setQueryData(['leaderboard'], payload)
      if (payload.error_message) notifyBad(payload.error_message)
      else notifyOk('榜单已刷新')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '刷新失败')),
  })

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">AIHOT CACHE</div>
            <h1 className="text-2xl font-semibold">模型榜</h1>
            <p className="mt-1 text-sm text-mist">AIHOT 总榜前 30 名以及本站覆盖率</p>
          </div>
          <Button
            type="button"
            variant="line"
            className="shrink-0 lg:hidden"
            disabled={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            <RefreshCw size={16} className={refreshMutation.isPending ? 'animate-spin' : undefined} />
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
            disabled={refreshMutation.isPending}
            onClick={() => refreshMutation.mutate()}
          >
            <RefreshCw size={16} className={refreshMutation.isPending ? 'animate-spin' : undefined} />
            刷新缓存
          </Button>
        </div>
      </div>

      <LeaderboardView
        data={data}
        isLoading={isLoading}
        isError={isError}
        error={error}
        emptyText="还没有缓存。点刷新从 AIHOT 拉取一次。"
        onRetry={() => void refetch()}
      />
    </div>
  )
}
