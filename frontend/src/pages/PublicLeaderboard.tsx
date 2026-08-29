import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { LeaderboardView } from '../components/LeaderboardView'
import { Button } from '../components/ui'
import { api } from '../lib/api'

export function PublicLeaderboardPage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['public-leaderboard'],
    queryFn: () => api.publicLeaderboard(),
  })

  return (
    <div className="page-enter min-h-svh bg-ink px-4 pt-[max(2.5rem,calc(env(safe-area-inset-top)+1.5rem))] pb-[max(2.5rem,calc(env(safe-area-inset-bottom)+1.5rem))]">
      <div className="mx-auto w-full max-w-6xl space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-xs tracking-[0.28em] text-signal">PIVOT DESK</div>
            <h1 className="mt-2 text-2xl font-semibold">模型榜</h1>
            <p className="mt-1 text-sm text-mist">AIHOT 总榜前 30 名以及本站覆盖情况。</p>
          </div>
          <a className="shrink-0" href="https://aihot.virxact.com/leaderboard" target="_blank" rel="noreferrer">
            <Button type="button" variant="line">
              <ExternalLink size={16} />
              原站
            </Button>
          </a>
        </div>

        <LeaderboardView
          data={data}
          isLoading={isLoading}
          isError={isError}
          error={error}
          emptyText="还没有缓存。请稍后再看。"
          onRetry={() => void refetch()}
        />
      </div>
    </div>
  )
}
