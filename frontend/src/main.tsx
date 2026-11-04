import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { whenThemeReady } from './lib/theme'

// iOS Safari 会忽略 viewport 的 user-scalable=no，用手势事件拦住双指缩放。
function blockIosPinchZoom() {
  const prevent = (event: Event) => event.preventDefault()
  document.addEventListener('gesturestart', prevent)
  document.addEventListener('gesturechange', prevent)
  document.addEventListener('gestureend', prevent)
}

blockIosPinchZoom()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// React 挂载完成后淡出并移除启动画面，避免 iOS 黑屏。
let splashDismissed = false
function dismissBootSplash() {
  if (splashDismissed) return
  splashDismissed = true
  const splash = document.getElementById('boot-splash')
  if (!splash) return
  splash.classList.add('boot-splash-hide')
  window.setTimeout(() => splash.remove(), 350)
}

// 等首帧渲染且服务端主题偏好落地后再淡出：
// 首帧只能按本地缓存猜主题，账号偏好回来可能换成另一个主题，提前揭开启动画面会闪一下。
async function revealApp() {
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
  await whenThemeReady()
  dismissBootSplash()
}
void revealApp()

// 兜底：后台标签页（或被 headless/自动化驱动的页面）里 requestAnimationFrame 会被浏览器
// 节流甚至完全不触发，只靠 rAF 会让启动画面永久盖在页面上。用一个定时器保证一定移除。
window.setTimeout(dismissBootSplash, 1800)
