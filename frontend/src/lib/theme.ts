import { useSyncExternalStore } from 'react'

/** 实际生效的主题 */
export type Theme = 'dark' | 'light'
/** 用户选择的主题模式：跟随系统或固定某个主题 */
export type ThemeMode = 'system' | 'light' | 'dark'

export const THEME_MODE_KEY = 'pivot:theme-mode'

const LEGACY_KEY = 'pivot:theme'
const THEME_COLOR: Record<Theme, string> = {
  dark: '#0b0d11',
  light: '#eef2f8',
}

const listeners = new Set<() => void>()
let mode: ThemeMode | null = null
let resolved: Theme | null = null

/**
 * 首帧脚本只能按本地缓存猜主题，登录后服务端偏好可能覆盖它。
 * 启动画面要等偏好落地后再淡出，避免「浅色闪一下再变深色」。
 */
const themeReadyWaiters: Array<() => void> = []
let themeReady = false

export function markThemeReady() {
  if (themeReady) return
  themeReady = true
  themeReadyWaiters.splice(0).forEach((resolve) => resolve())
}

/** 主题已确定（或等待超时）时 resolve，供启动画面延迟淡出使用。 */
export function whenThemeReady(timeoutMs = 900): Promise<void> {
  if (themeReady) return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(() => markThemeReady(), timeoutMs)
    themeReadyWaiters.push(done)
  })
}

function normalizeMode(value: string | null | undefined): ThemeMode {
  return value === 'light' || value === 'dark' ? value : 'system'
}

function systemTheme(): Theme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark'
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function storedMode(): ThemeMode {
  if (typeof localStorage === 'undefined') return 'system'
  try {
    const saved = localStorage.getItem(THEME_MODE_KEY)
    if (saved) return normalizeMode(saved)
    // 兼容旧版本只存了具体主题的键
    const legacy = localStorage.getItem(LEGACY_KEY)
    return legacy === 'light' || legacy === 'dark' ? legacy : 'system'
  } catch {
    return 'system'
  }
}

export function getThemeMode(): ThemeMode {
  if (mode) return mode
  if (typeof document === 'undefined') return 'system'
  // 首帧脚本已经把模式写到 data-theme-mode，优先用它避免与服务端不一致时闪烁
  mode = normalizeMode(document.documentElement.dataset.themeMode ?? storedMode())
  return mode
}

export function getResolvedTheme(): Theme {
  if (resolved) return resolved
  resolved = getThemeMode() === 'system' ? systemTheme() : (getThemeMode() as Theme)
  return resolved
}

function syncDocument(nextMode: ThemeMode) {
  const nextTheme: Theme = nextMode === 'system' ? systemTheme() : nextMode
  resolved = nextTheme
  mode = nextMode
  const root = document.documentElement
  root.dataset.theme = nextTheme
  root.dataset.themeMode = nextMode
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[nextTheme])
}

/**
 * 切换主题模式。
 * persistLocal=true 时写入 localStorage，供首帧脚本在样式表加载前读出，避免闪屏。
 */
export function setThemeMode(next: ThemeMode, options: { persistLocal?: boolean } = {}) {
  const { persistLocal = true } = options
  syncDocument(next)
  if (persistLocal) {
    try {
      localStorage.setItem(THEME_MODE_KEY, next)
    } catch {
      // 隐私模式下 localStorage 不可写，仅在当前会话生效
    }
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(subscribe, getThemeMode, () => 'system')
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getResolvedTheme, () => 'dark')
}

if (typeof window !== 'undefined') {
  // 跟随系统时，系统外观变化要即时生效
  if (typeof window.matchMedia === 'function') {
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const onSystemChange = () => {
      if (getThemeMode() !== 'system') return
      syncDocument('system')
      listeners.forEach((listener) => listener())
    }
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', onSystemChange)
    } else if (typeof media.addListener === 'function') {
      media.addListener(onSystemChange)
    }
  }

  // 多标签页同步：任一标签切换后其余标签跟着更新
  window.addEventListener('storage', (event) => {
    if (event.key !== THEME_MODE_KEY || event.newValue === null) return
    const next = normalizeMode(event.newValue)
    if (next === getThemeMode()) return
    syncDocument(next)
    listeners.forEach((listener) => listener())
  })
}
