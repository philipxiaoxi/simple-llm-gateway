import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Download, Gauge, KeyRound, LogOut, Menu, MessageSquareText, Mic, RadioTower, ServerCog, ShieldAlert, Sparkles, Timer, Trophy, UserRoundPen, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, clearToken, setToken } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { MIN_PASSWORD_LENGTH, cn, errorMessage } from '../lib/utils'
import { ForceUpdateButton } from './PwaUpdate'
import { Button, Dialog, Field, Input } from './ui'

const links = [
  { to: '/', label: '概览', icon: Activity },
  { to: '/accounts', label: '上游账号', icon: RadioTower },
  { to: '/agents', label: '网关代理', icon: ServerCog },
  { to: '/keys', label: 'API Key', icon: KeyRound },
  { to: '/skills', label: 'Skills', icon: Sparkles },
  { to: '/tools', label: '工具中心', icon: Download },
  { to: '/benchmark', label: '模型测速', icon: Gauge },
  { to: '/benchmark/history', label: '测速历史', icon: Activity },
  { to: '/leaderboard', label: '模型榜', icon: Trophy },
  { to: '/jobs', label: '定时任务', icon: Timer },
  { to: '/logs', label: '记录审计', icon: MessageSquareText },
  { to: '/content-audit', label: '内容审计', icon: ShieldAlert },
  { to: '/voice', label: '语音输入', icon: Mic },
]

const tabLinks = [
  { to: '/', label: '概览', icon: Activity },
  { to: '/accounts', label: '账号', icon: RadioTower },
  { to: '/keys', label: 'Key', icon: KeyRound },
  { to: '/skills', label: 'Skills', icon: Sparkles },
  { to: '/logs', label: '记录', icon: MessageSquareText },
]

function isPathActive(to: string, pathname: string) {
  if (to === '/') return pathname === '/'
  return pathname === to || pathname.startsWith(`${to}/`)
}

function currentPageLabel(pathname: string) {
  const ranked = [...links].sort((left, right) => right.to.length - left.to.length)
  const match = ranked.find((link) => isPathActive(link.to, pathname))
  return match?.label ?? '控制台'
}

function ProfileDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [username, setUsername] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const currentName = data?.username ?? ''

  async function submit() {
    const nextName = username.trim()
    if (!currentPassword) {
      setError('请填写当前密码')
      return
    }
    if (!nextName && !password) {
      setError('请填写新用户名或新密码')
      return
    }
    if (password && password.length < MIN_PASSWORD_LENGTH) {
      setError('新密码至少 8 位')
      return
    }
    if (password && password !== confirm) {
      setError('两次新密码不一致')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.updateMe({
        current_password: currentPassword,
        username: nextName && nextName !== currentName ? nextName : undefined,
        password: password || undefined,
      })
      setToken(result.token)
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      notifyOk('管理员账号已更新')
      onClose()
    } catch (caught) {
      setError(errorMessage(caught, '更新失败'))
      notifyBad(errorMessage(caught, '更新失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="修改管理员账号" onClose={onClose}>
      <div className="grid gap-3">
        <Field label="新用户名（不改请留空）">
          <Input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="不改请留空"
            autoComplete="off"
          />
        </Field>
        <Field label="当前密码">
          <Input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        </Field>
        <Field label="新密码（不改请留空）">
          <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        <Field label="确认新密码">
          <Input type="password" value={confirm} onChange={(event) => setConfirm(event.target.value)} />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void submit()}>
            保存
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function useMobileSwipeDrawer(open: boolean, setOpen: (next: boolean) => void) {
  useEffect(() => {
    const mobile = window.matchMedia('(max-width: 1023px)')
    let startX = 0
    let startY = 0
    let tracking = false

    function isIgnored(target: EventTarget | null) {
      return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
    }

    function onStart(event: TouchEvent) {
      if (!mobile.matches || event.touches.length !== 1 || isIgnored(event.target)) {
        tracking = false
        return
      }
      const touch = event.touches[0]
      const height = window.innerHeight
      if (touch.clientX < 20) return
      if (touch.clientY < 72 || touch.clientY > height - 88) return
      startX = touch.clientX
      startY = touch.clientY
      tracking = true
    }

    function onMove(event: TouchEvent) {
      if (!tracking || event.touches.length !== 1) return
      const touch = event.touches[0]
      const dx = touch.clientX - startX
      const dy = touch.clientY - startY
      if (Math.abs(dy) > 36 && Math.abs(dy) > Math.abs(dx)) {
        tracking = false
        return
      }
      if (!open && dx > 56 && Math.abs(dy) < 40) {
        tracking = false
        setOpen(true)
      }
      if (open && dx < -56 && Math.abs(dy) < 40) {
        tracking = false
        setOpen(false)
      }
    }

    function onEnd() {
      tracking = false
    }

    document.addEventListener('touchstart', onStart, { passive: true })
    document.addEventListener('touchmove', onMove, { passive: true })
    document.addEventListener('touchend', onEnd)
    document.addEventListener('touchcancel', onEnd)
    return () => {
      document.removeEventListener('touchstart', onStart)
      document.removeEventListener('touchmove', onMove)
      document.removeEventListener('touchend', onEnd)
      document.removeEventListener('touchcancel', onEnd)
    }
  }, [open, setOpen])
}

export function Layout() {
  const [open, setOpen] = useState(false)
  const [profile, setProfile] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const location = useLocation()
  const mainRef = useRef<HTMLElement>(null)
  useMobileSwipeDrawer(open, setOpen)

  useEffect(() => {
    // Instant reset; also interrupts any leftover smooth scroll from previous pages.
    const main = mainRef.current
    if (main) {
      main.style.scrollBehavior = 'auto'
      main.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    }
    const root = document.documentElement
    const previous = root.style.scrollBehavior
    root.style.scrollBehavior = 'auto'
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
    root.style.scrollBehavior = previous
  }, [location.pathname, location.search])

  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [open])

  function logout() {
    clearToken()
    void queryClient.clear()
    navigate('/login')
  }

  return (
    <div className="min-h-screen min-h-lvh bg-ink lg:h-svh lg:min-h-0 lg:overflow-hidden lg:grid lg:grid-cols-[240px_1fr]">
      <header className="fixed inset-x-0 top-0 z-30 border-b border-line bg-panel/95 pt-[env(safe-area-inset-top)] backdrop-blur-md lg:hidden">
        <div className="flex h-12 items-center justify-between px-2">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-paper"
            aria-label="打开菜单"
          >
            <Menu size={20} />
          </button>
          <div className="min-w-0 truncate font-medium">{currentPageLabel(location.pathname)}</div>
          <div className="min-w-11" aria-hidden="true" />
        </div>
      </header>

      <div
        className={cn(
          'fixed inset-0 z-40 bg-black/60 transition-opacity duration-300 lg:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />

      <aside
        className={cn(
          'safe-area-drawer fixed inset-y-0 left-0 z-50 flex w-72 max-w-[80vw] flex-col border-r border-line bg-panel transition-transform duration-300 ease-out lg:static lg:z-auto lg:h-svh lg:w-auto lg:max-w-none lg:translate-x-0 lg:overflow-hidden lg:bg-panel/80',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-4 lg:hidden">
          <div className="font-mono text-xs tracking-[0.28em] text-signal">PIVOT DESK</div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-mist hover:text-paper"
            aria-label="关闭菜单"
          >
            <X size={20} />
          </button>
        </div>
        <div className="hidden border-b border-line px-5 py-6 lg:block">
          <div className="font-mono text-xs tracking-[0.28em] text-signal">PIVOT DESK</div>
          <div className="mt-1 text-lg font-semibold">AI一体化服务平台</div>
        </div>
        <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-3">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === '/' || link.to === '/benchmark'}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-md px-3 py-3 text-sm lg:py-2',
                  isActive ? 'bg-signal/15 text-signal' : 'text-mist hover:bg-white/5 hover:text-paper',
                )
              }
            >
              <link.icon size={16} />
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-1 border-t border-line p-2 lg:border-t-0">
          <ForceUpdateButton className="min-w-11 flex-1 justify-center px-0 text-mist" title="强制更新">
            <span className="sr-only">强制更新</span>
          </ForceUpdateButton>
          <Button
            variant="ghost"
            className="min-w-11 flex-1 justify-center px-0 text-mist"
            onClick={() => setProfile(true)}
            title="修改账号"
            aria-label="修改账号"
          >
            <UserRoundPen size={16} />
          </Button>
          <Button
            variant="ghost"
            className="min-w-11 flex-1 justify-center px-0 text-mist"
            onClick={logout}
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut size={16} />
          </Button>
        </div>
      </aside>
      {profile ? <ProfileDialog onClose={() => setProfile(false)} /> : null}
      <main ref={mainRef} className="bg-ink px-4 pb-[calc(var(--app-tab)+env(safe-area-inset-bottom)+0.75rem)] pt-[calc(var(--app-header)+env(safe-area-inset-top)+0.75rem)] lg:h-svh lg:overflow-y-auto lg:px-8 lg:pb-6 lg:pt-6">
        <Outlet />
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden">
        <div className="grid h-14 grid-cols-5">
          {tabLinks.map((link) => {
            const active = isPathActive(link.to, location.pathname)
            return (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                onClick={() => setOpen(false)}
                className={cn(
                  'flex min-h-11 flex-col items-center justify-center gap-0.5 text-[11px] leading-none',
                  active ? 'text-signal' : 'text-mist',
                )}
              >
                <link.icon size={18} />
                {link.label}
              </NavLink>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
