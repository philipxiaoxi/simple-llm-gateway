import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { OfflineTool } from '../components/offline/OfflineTool'
import { Button } from '../components/ui'
import { api, type InfoPublicGateStatus } from '../lib/api'
import { PublicInfoGate, PublicInfoWatermark } from './PublicInfo'

/** 对外公开的离线下载页：复用资讯公开页的口令门禁与水印，走公开令牌化接口。 */
export function PublicOfflinePage() {
  const queryClient = useQueryClient()
  const gate = useQuery({ queryKey: ['public-gate', 'offline'], queryFn: () => api.publicGate('offline'), retry: false })
  const locked = Boolean(gate.data?.required && !gate.data.unlocked)

  async function lockNow() {
    try {
      await api.publicGateLock('offline')
    } catch {
      /* ignore */
    }
    queryClient.setQueryData<InfoPublicGateStatus>(['public-gate', 'offline'], {
      required: true,
      unlocked: false,
      watermark: null,
    })
  }

  if (gate.isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center bg-ink text-sm text-mist">加载中…</div>
    )
  }

  if (locked) {
    return (
      <div className="page-enter flex min-h-svh items-center justify-center bg-ink px-4">
        <PublicInfoGate scope="offline" onUnlocked={() => undefined} />
      </div>
    )
  }

  return (
    <div className="page-enter mx-auto w-full max-w-[1080px] space-y-5 px-4 pt-6 pb-[max(3rem,calc(env(safe-area-inset-bottom)+2rem))]">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-xs tracking-[0.28em] text-signal">OFFLINE TOOLKIT</div>
          <h1 className="mt-2 text-2xl font-semibold">离线下载</h1>
          <p className="mt-1 text-xs text-mist">
            VSCode 插件 / Chrome / Edge 扩展 / Docker 镜像 / Microsoft Store，一键转离线安装包。
          </p>
        </div>
        {gate.data?.required ? (
          <Button type="button" variant="ghost" onClick={lockNow}>
            <Lock size={15} /> 退出
          </Button>
        ) : null}
      </header>
      <OfflineTool base="/api/public/offline" />
      <PublicInfoWatermark scope="offline" />
    </div>
  )
}

export default PublicOfflinePage
