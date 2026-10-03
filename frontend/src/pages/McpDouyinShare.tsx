import { useQuery } from '@tanstack/react-query'
import { Download, Share2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText } from '../lib/utils'

const SPLASH_MS = 1000

function Splash() {
  return (
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 bg-ink px-6 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-signal to-fuchsia-500 text-2xl font-black text-white shadow-lg">
        抖
      </div>
      <h1 className="text-xl font-semibold text-paper">抖音跨平台分享功能</h1>
    </div>
  )
}

function Notice({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="flex h-[100dvh] items-center justify-center bg-ink px-6 text-center">
      <div className="space-y-2">
        <h1 className="text-lg font-semibold text-paper">{title}</h1>
        <p className="text-sm text-mist">{desc}</p>
      </div>
    </div>
  )
}

export function McpDouyinSharePage() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [showSplash, setShowSplash] = useState(true)

  const meta = useQuery({
    queryKey: ['douyin-share', token],
    queryFn: () => api.douyinShareMeta(token),
    enabled: Boolean(token),
    retry: false,
  })

  useEffect(() => {
    const timer = window.setTimeout(() => setShowSplash(false), SPLASH_MS)
    return () => window.clearTimeout(timer)
  }, [])

  async function onShare() {
    try {
      await copyText(window.location.href)
      notifyOk('链接已复制，可粘贴到微信打开')
    } catch {
      notifyBad('复制失败，请手动复制地址栏链接')
    }
  }

  if (showSplash) return <Splash />

  if (!token) return <Notice title="链接无效或已过期" desc="请向分享者重新获取预览链接。" />
  if (meta.isLoading) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-ink">
        <div className="h-7 w-7 animate-spin rounded-full border-[3px] border-signal/20 border-t-signal" />
      </div>
    )
  }
  if (meta.isError || !meta.data) {
    return <Notice title="链接无效或已过期" desc="令牌已过期或媒体已被清理，请重新获取分享链接。" />
  }

  const data = meta.data
  const src = `${window.location.origin}${data.download_url}`

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-ink">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-paper">{data.title || '抖音作品'}</div>
          <div className="text-xs text-mist">抖音跨平台分享</div>
        </div>
        <button
          type="button"
          onClick={onShare}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line px-3 py-1.5 text-xs text-paper hover:border-signal/50"
        >
          <Share2 size={14} /> 分享
        </button>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center bg-black p-2">
        {data.kind === 'image' ? (
          <img
            src={src}
            alt={data.title}
            className="max-h-full max-w-full object-contain"
            referrerPolicy="no-referrer"
          />
        ) : data.kind === 'audio' ? (
          <audio controls autoPlay preload="auto" src={src} className="w-full max-w-lg">
            <track kind="captions" />
          </audio>
        ) : (
          <video controls autoPlay playsInline src={src} className="max-h-full max-w-full object-contain">
            <track kind="captions" />
          </video>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-center gap-3 border-t border-line px-4 py-3">
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-line bg-panel-2 px-3 py-1.5 text-xs text-paper hover:border-mist/40"
        >
          <Download size={14} /> 直接打开 / 下载
        </a>
      </div>
    </div>
  )
}

export default McpDouyinSharePage
