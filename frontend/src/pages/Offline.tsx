import { useState } from 'react'
import { ExternalLink, Link2, Lock } from 'lucide-react'
import { OfflineTool } from '../components/offline/OfflineTool'
import { InfoPublicGateDialog } from '../components/InfoPublicGateDialog'
import { Button } from '../components/ui'
import { notifyOk } from '../lib/toast'
import { copyText } from '../lib/utils'

const PUBLIC_OFFLINE_PATH = '/share/offline'

/** 管理端离线下载页：与公开页共用同一套后端接口，走管理员鉴权。 */
export function OfflinePage() {
  const [gateOpen, setGateOpen] = useState(false)

  function publicUrl() {
    return `${window.location.origin}${PUBLIC_OFFLINE_PATH}`
  }

  return (
    <div className="page-enter mx-auto w-full max-w-[1080px] space-y-5 px-4 pt-6 pb-[max(3rem,calc(env(safe-area-inset-bottom)+2rem))]">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-xs tracking-[0.28em] text-signal">OFFLINE TOOLKIT</div>
          <h1 className="mt-2 text-2xl font-semibold">离线下载</h1>
          <p className="mt-1 text-xs text-mist">
            把 VSCode 插件 / Chrome / Edge 扩展 / Docker 镜像 / Microsoft Store 应用转成可离线使用的安装包或直链。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="line" onClick={() => window.open(publicUrl(), '_blank', 'noopener,noreferrer')}>
            <ExternalLink size={15} /> 公开页
          </Button>
          <Button
            type="button"
            variant="line"
            onClick={() => {
              void copyText(publicUrl()).then(() => notifyOk('已复制公开页链接'))
            }}
          >
            <Link2 size={15} /> 复制链接
          </Button>
          <Button type="button" variant="line" onClick={() => setGateOpen(true)}>
            <Lock size={15} /> 门禁设置
          </Button>
        </div>
      </header>
      <OfflineTool base="/api/admin/offline" />
      {gateOpen ? <InfoPublicGateDialog open scope="offline" onClose={() => setGateOpen(false)} /> : null}
    </div>
  )
}

export default OfflinePage
