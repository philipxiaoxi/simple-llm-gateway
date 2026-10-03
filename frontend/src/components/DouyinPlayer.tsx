import Player from 'xgplayer'
import 'xgplayer/dist/index.min.css'
import { useEffect, useRef, useState } from 'react'

type PlayerInstance = {
  play: () => Promise<void> | null | undefined
  destroy: () => void
  on: (event: string, handler: () => void) => void
  once: (event: string, handler: () => void) => void
  muted: boolean
}

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
        autoplayMuted: autoPlay,
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

    const applyMuted = (muted: boolean) => {
      if (!player || !alive) return
      player.muted = muted
      setShowUnmute(muted)
    }

    const tryAutoplay = () => {
      if (!player || !autoPlay) return
      const result = player.play()
      if (result && typeof result.catch === 'function') {
        void result.catch(() => {
          applyMuted(true)
          const retry = playerRef.current?.play()
          if (retry && typeof retry.catch === 'function') {
            void retry.catch(() => undefined)
          }
        })
      }
    }

    player.once('canplay', tryAutoplay)
    player.on('error', () => {
      if (!alive) return
      setFailed(true)
    })

    const onWeixinReady = () => tryAutoplay()
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
        muted={autoPlay}
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
