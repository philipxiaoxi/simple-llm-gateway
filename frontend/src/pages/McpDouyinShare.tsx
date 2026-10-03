import { useQuery } from '@tanstack/react-query'
import { Download, Share2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DouyinPlayer } from '../components/DouyinPlayer'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, douyinShareText } from '../lib/utils'

const SPLASH_MS = 1100
const SPLASH_FADE_MS = 320

function Splash({ closing }: { closing: boolean }) {
  return (
    <div
      className={`fixed inset-0 z-50 overflow-hidden bg-ink transition-opacity duration-300 ${
        closing ? 'pointer-events-none opacity-0' : 'opacity-100'
      }`}
    >
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -top-1/3 left-1/2 h-[75vmax] w-[75vmax] -translate-x-1/2 rounded-full bg-signal/10 blur-[130px]" />
        <div className="absolute -bottom-1/4 right-0 h-[45vmax] w-[45vmax] translate-x-1/4 rounded-full bg-info/10 blur-[110px]" />
        <div className="absolute inset-0 opacity-[0.5] [background-image:linear-gradient(rgba(255,255,255,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.03)_1px,transparent_1px)] [background-size:44px_44px] [mask-image:radial-gradient(60%_50%_at_50%_45%,black,transparent)]" />
      </div>

      <div className="relative flex h-full flex-col items-center justify-center gap-7 px-8 text-center">
        <div className="relative isolate">
          <div className="absolute -inset-3 rounded-[32px] bg-gradient-to-tr from-signal/40 via-fuchsia-400/30 to-info/40 opacity-70 blur-xl" />
          <div className="relative flex h-20 w-20 items-center justify-center rounded-[24px] border border-white/10 bg-gradient-to-br from-[#1c2331] to-[#0d1117] shadow-2xl shadow-black/50">
            <span className="bg-gradient-to-br from-paper via-paper to-mist bg-clip-text text-[2rem] font-black tracking-tighter text-transparent">
              抖
            </span>
          </div>
        </div>

        <div className="space-y-3">
          <h1 className="text-2xl font-semibold tracking-tight text-paper">抖音跨平台分享功能</h1>
          <p className="text-[0.7rem] font-medium uppercase tracking-[0.42em] text-mist">
            Douyin · Cross Platform
          </p>
        </div>

        <div className="h-[3px] w-44 overflow-hidden rounded-full bg-white/[0.06]">
          <div className="douyin-splash-sweep h-full w-1/3 rounded-full bg-gradient-to-r from-transparent via-signal to-transparent" />
        </div>
      </div>
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
  const [splashClosing, setSplashClosing] = useState(false)

  const meta = useQuery({
    queryKey: ['douyin-share', token],
    queryFn: () => api.douyinShareMeta(token),
    enabled: Boolean(token),
    retry: false,
  })

  useEffect(() => {
    const fade = window.setTimeout(() => setSplashClosing(true), SPLASH_MS)
    const done = window.setTimeout(() => setShowSplash(false), SPLASH_MS + SPLASH_FADE_MS)
    return () => {
      window.clearTimeout(fade)
      window.clearTimeout(done)
    }
  }, [])

  async function onShare() {
    const link = window.location.href
    try {
      await copyText(douyinShareText(link, { title: meta.data?.title, kind: meta.data?.kind }))
      notifyOk('分享文案已复制，可粘贴到微信打开')
    } catch {
      notifyBad('复制失败，请手动复制地址栏链接')
    }
  }

  if (showSplash) return <Splash closing={splashClosing} />

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
          <DouyinPlayer src={src} poster={data.cover_url} autoPlay className="h-full w-full" />
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
