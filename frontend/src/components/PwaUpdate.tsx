import type { ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { registerSW } from 'virtual:pwa-register'
import { checkServiceWorkerUpdate, forceAppUpdate } from '../lib/pwa'
import { notifyBad, notifyInfo } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Button } from './ui'

const CHECK_INTERVAL_MS = 60_000

export function PwaUpdater() {
  useEffect(() => {
    let cancelled = false
    let timer = 0
    let idleHandle: number | null = null
    const start = () => {
      if (cancelled) return
      registerSW({
        onRegisteredSW(_scriptUrl, registration) {
          if (cancelled || !registration) return
          timer = window.setInterval(() => {
            void registration.update()
          }, CHECK_INTERVAL_MS)
        },
      })
    }
    // 首屏 load 之后再注册 SW（优先空闲时机），避免安装与预缓存和首屏资源抢带宽。
    const schedule = () => {
      if (cancelled) return
      if (typeof window.requestIdleCallback === 'function') {
        idleHandle = window.requestIdleCallback(start, { timeout: 3000 })
      } else {
        start()
      }
    }
    if (document.readyState === 'complete') schedule()
    else window.addEventListener('load', schedule, { once: true })

    const onVisible = () => {
      if (document.visibilityState === 'visible') checkServiceWorkerUpdate()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', checkServiceWorkerUpdate)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      if (idleHandle !== null && typeof window.cancelIdleCallback === 'function') {
        window.cancelIdleCallback(idleHandle)
      }
      window.removeEventListener('load', schedule)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', checkServiceWorkerUpdate)
    }
  }, [])
  return null
}

export function ForceUpdateButton({
  className,
  children,
  title,
}: {
  className?: string
  children?: ReactNode
  title?: string
}) {
  const [pending, setPending] = useState(false)

  async function onClick() {
    setPending(true)
    notifyInfo('正在清除本地缓存并刷新页面…')
    try {
      await forceAppUpdate()
    } catch (error) {
      setPending(false)
      notifyBad(errorMessage(error, '强制更新失败'))
    }
  }

  return (
    <Button variant="ghost" className={className} disabled={pending} onClick={() => void onClick()} title={title} aria-label={title}>
      <RefreshCw size={16} />
      {children ?? (pending ? '更新中…' : '强制更新')}
    </Button>
  )
}
