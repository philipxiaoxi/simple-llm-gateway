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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">AIHOT CACHE</div>
          <h1 className="text-2xl font-semibold">模型榜</h1>
          <p className="mt-1 text-sm text-mist">
            AIHOT 总榜前 30 名以及本站覆盖率
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="line"
            onClick={() => {
              void navigator.clipboard.writeText(`${window.location.origin}/share/leaderboard`).then(
                () => notifyOk('已复制公开模型榜地址'),
                () => notifyBad('复制失败'),
              )
            }}
          >
            复制公开页
          </Button>
          <Button type="button" variant="line" onClick={() => window.open('/share/leaderboard', '_blank', 'noopener')}>
            打开公开页
          </Button>
          <a href="https://aihot.virxact.com/leaderboard" target="_blank" rel="noreferrer">
            <Button type="button" variant="line">
              <ExternalLink size={16} />
              原站
            </Button>
          </a>
          <Button
            type="button"
            variant="line"
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
