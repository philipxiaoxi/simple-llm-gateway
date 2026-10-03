import Player from 'xgplayer'
import 'xgplayer/dist/index.min.css'
import { useEffect, useRef, useState } from 'react'

type PlayerInstance = {
  play: () => Promise<void> | null | undefined
  destroy: () => void
  on: (event: string, handler: (payload?: unknown) => void) => void
  muted: boolean
}

type UserAction = { action?: string; to?: boolean }

type Props = {
  src: string
  poster?: string | null
  autoPlay?: boolean
  className?: string
}

export function DouyinPlayer({ src, poster, autoPlay = false, className }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const playerRef = useRef<PlayerInstance | null>(null)
  const [showUnmute, setShowUnmute] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let alive = true
    let player: PlayerInstance | null = null

    try {
      player = new Player({
        el: host,
        url: src,
        poster: poster || undefined,
        autoplay: autoPlay,
        volume: 0.8,
        playsinline: true,
        width: '100%',
        height: '100%',
        lang: 'zh-cn',
        controls: true,
        closeVideoClick: false,
        ignores: ['download', 'pip', 'keyboard'],
        videoAttributes: {
          playsinline: 'true',
          'webkit-playsinline': 'true',
          'x5-playsinline': 'true',
          'x5-video-player-type': 'h5',
          'x5-video-player-fullscreen': 'false',
        },
      } as unknown as ConstructorParameters<typeof Player>[0]) as unknown as PlayerInstance
    } catch {
      setFailed(true)
      return
    }

    playerRef.current = player

    const play = () => {
      const result = playerRef.current?.play()
      if (result && typeof result.catch === 'function') {
        void result.catch(() => undefined)
      }
    }

    // 自动播放（有声）被浏览器拦截：降级为静音播放并提示一键开声
    player.on('autoplay_was_prevented', () => {
      if (!alive || !player) return
      player.muted = true
      setShowUnmute(true)
      play()
    })

    // 用户主动点击播放时恢复声音
    player.on('user_action', (payload) => {
      if (!alive || !player) return
      const action = payload as UserAction | undefined
      if (action?.action === 'switch_play_pause' && action.to === true && player.muted) {
        player.muted = false
        setShowUnmute(false)
      }
    })

    player.on('error', () => {
      if (!alive) return
      setFailed(true)
    })

    // 微信 iOS：桥接就绪后再尝试播放
    const onWeixinReady = () => play()
    document.addEventListener('WeixinJSBridgeReady', onWeixinReady, false)

    return () => {
      alive = false
      document.removeEventListener('WeixinJSBridgeReady', onWeixinReady, false)
      try {
        player?.destroy()
      } catch {
        void 0
      }
      playerRef.current = null
    }
  }, [src, poster, autoPlay])

  function unmute() {
    const player = playerRef.current
    if (!player) return
    player.muted = false
    setShowUnmute(false)
    void player.play()
  }

  if (failed) {
    return (
      <video
        controls
        autoPlay={autoPlay}
        playsInline
        preload="metadata"
        src={src}
        poster={poster || undefined}
        className={className}
      >
        <track kind="captions" />
      </video>
    )
  }

  return (
    <div className={className ? `relative ${className}` : 'relative'}>
      <div ref={hostRef} className="h-full w-full" />
      {showUnmute ? (
        <button
          type="button"
          onClick={unmute}
          className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/15 bg-black/55 px-4 py-2 text-xs font-medium text-paper backdrop-blur-md transition hover:border-signal/60 hover:text-signal"
        >
          点击开启声音
        </button>
      ) : null}
    </div>
  )
}

export default DouyinPlayer
