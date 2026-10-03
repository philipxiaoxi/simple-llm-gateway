import Player from 'xgplayer'
import 'xgplayer/dist/index.min.css'
import { useEffect, useRef, useState } from 'react'

type PlayerInstance = {
  play: () => Promise<void> | null | undefined
  destroy: () => void
  on: (event: string, handler: (payload?: unknown) => void) => void
  media?: HTMLMediaElement | null
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
  const [showSoundHint, setShowSoundHint] = useState(false)
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
      const result = playerRef.current?.media?.play() ?? playerRef.current?.play()
      if (result && typeof result.catch === 'function') {
        void result.catch(() => undefined)
      }
    }

    // 直接操作媒体元素解锁声音，避免播放器内部状态回写
    const unmute = () => {
      const media = playerRef.current?.media
      if (!media || !media.muted) return
      media.muted = false
      if (media.volume < 0.8) media.volume = 0.8
      setShowSoundHint(false)
    }

    // 自动播放（有声）被浏览器拦截：降级为静音播放，并提示轻触开声
    player.on('autoplay_was_prevented', () => {
      const media = playerRef.current?.media
      if (!alive || !media) return
      media.muted = true
      setShowSoundHint(true)
      play()
    })

    // 用户任何一次主动点按都视为交互手势：若静音则解锁声音（不改变播放/暂停）
    const onGesture = () => {
      if (alive) unmute()
    }
    host.addEventListener('pointerdown', onGesture, true)
    host.addEventListener('touchstart', onGesture, true)

    // 兜底：播放器自身的用户动作事件（点击播放/暂停）
    player.on('user_action', (payload) => {
      if (!alive) return
      const action = payload as UserAction | undefined
      if (action?.action === 'switch_play_pause' && action.to === true) unmute()
    })

    player.on('volumechange', () => {
      if (!alive) return
      if (playerRef.current?.media && !playerRef.current.media.muted) setShowSoundHint(false)
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
      host.removeEventListener('pointerdown', onGesture, true)
      host.removeEventListener('touchstart', onGesture, true)
      document.removeEventListener('WeixinJSBridgeReady', onWeixinReady, false)
      try {
        player?.destroy()
      } catch {
        void 0
      }
      playerRef.current = null
    }
  }, [src, poster, autoPlay])

  function enableSound(event: React.MouseEvent<HTMLButtonElement>) {
    event.stopPropagation()
    const media = playerRef.current?.media
    if (!media) return
    media.muted = false
    if (media.volume < 0.8) media.volume = 0.8
    void media.play().catch(() => undefined)
    setShowSoundHint(false)
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
      {showSoundHint ? (
        <button
          type="button"
          onClick={enableSound}
          className="absolute left-3 top-3 z-10 flex items-center gap-1.5 rounded-full border border-white/15 bg-black/60 px-3 py-1 text-xs font-medium text-paper backdrop-blur-md transition hover:border-signal/60 hover:text-signal"
        >
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-signal" />
          点击开启声音
        </button>
      ) : null}
    </div>
  )
}

export default DouyinPlayer
