import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Rows3,
  Search,
  UserRoundPen,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, clearToken, setToken } from '../lib/api'
import { flush, track } from '../lib/security'
import { notifyBad, notifyOk } from '../lib/toast'
import { MIN_PASSWORD_LENGTH, cn, errorMessage } from '../lib/utils'
import { useMediaQuery } from '../lib/useMediaQuery'
import { CommandPalette } from './command-palette'
import { PageContainer } from './layout-primitives'
import { NAV_GROUPS, TAB_LINKS, currentPageLabel, filterNavGroups, filterNavLinks, isPathActive } from './nav'
import { useSession } from '../lib/session'
import { ForceUpdateButton } from './PwaUpdate'
import { Button, Dialog, Field, Input } from './ui'

type Density = 'comfortable' | 'compact'

const BRAND_TILE =
  'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-signal/30 bg-signal/10 font-mono text-[11px] tracking-[0.16em] text-signal'

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
        {error ? <div className="text-sm text-danger" role="alert">{error}</div> : null}
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

export function Layout() {
  const [open, setOpen] = useState(false)
  const [profile, setProfile] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [drawerQuery, setDrawerQuery] = useState('')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('pivot:sidebar-collapsed') === '1')
  const [closedGroups, setClosedGroups] = useState<Set<string>>(() => new Set())
  const [density, setDensity] = useState<Density>(() =>
    localStorage.getItem('pivot:density') === 'compact' ? 'compact' : 'comfortable',
  )
  const isDesktop = useMediaQuery('(min-width: 768px)')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const location = useLocation()
  const mainRef = useRef<HTMLElement>(null)
  const { can } = useSession()
  const navGroups = useMemo(() => filterNavGroups(NAV_GROUPS, can), [can])
  const tabLinks = useMemo(() => filterNavLinks(TAB_LINKS, can), [can])

  const previousPathRef = useRef<string | null>(null)

  useEffect(() => {
    localStorage.setItem('pivot:sidebar-collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  useEffect(() => {
    localStorage.setItem('pivot:density', density)
    document.documentElement.dataset.density = density
  }, [density])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen((value) => !value)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const previousPath = previousPathRef.current
    previousPathRef.current = location.pathname
    // 详情浮层是叠在列表之上的子路由（/info -> /info/:id）：打开或关闭它都不该把
    // 瀑布流滚回顶部，否则用户每次看完一条都要重新找位置。
    const isOverlayTransition =
      (previousPath === '/info' && location.pathname.startsWith('/info/')) ||
      (Boolean(previousPath?.startsWith('/info/')) && location.pathname === '/info')
    // 记录详情会一次性消费 ?seq/?hl 定位到某条消息，随后用 replace 清掉这两个参数；
    // 若把这种同路径的 search 变化也当成导航，会把刚定位好的滚动位置顶回顶部。
    const isLogDetailReselect = previousPath === location.pathname && location.pathname.startsWith('/logs/')
    if (isOverlayTransition || isLogDetailReselect) return

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

  useEffect(() => {
    setOpen(false)
    setDrawerQuery('')
  }, [location.pathname])

  // 当前页所在分组永远保持展开（避免用户收起后找不到自己）
  const activeGroup = navGroups.find((group) =>
    group.links.some((link) => isPathActive(link.to, location.pathname)),
  )?.label
  useEffect(() => {
    if (!activeGroup) return
    setClosedGroups((previous) => {
      if (!previous.has(activeGroup)) return previous
      const next = new Set(previous)
      next.delete(activeGroup)
      return next
    })
  }, [activeGroup])

  // 路由变化、分组收起/展开后：把「当前分组」滚进可视区（连带组标题，避免只剩半截列表）
  useEffect(() => {
    const node = document.querySelector<HTMLElement>('aside nav [data-active-group="true"]')
    node?.scrollIntoView({ block: 'nearest' })
  }, [location.pathname, collapsed])

  function logout() {
    track('logout')
    flush()
    clearToken()
    void queryClient.clear()
    navigate('/login')
  }

  const normalizedQuery = drawerQuery.trim().toLowerCase()
  const visibleGroups = navGroups.map((group) => ({
    ...group,
    links: normalizedQuery
      ? group.links.filter((link) =>
          `${link.label} ${link.to} ${link.keywords ?? ''}`.toLowerCase().includes(normalizedQuery),
        )
      : group.links,
  })).filter((group) => group.links.length > 0)

  function toggleGroup(label: string) {
    setClosedGroups((previous) => {
      const next = new Set(previous)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })
  }

  return (
    <div
      className={cn(
        'min-h-screen min-h-lvh bg-ink md:grid md:h-svh md:min-h-0 md:overflow-hidden',
        collapsed ? 'md:grid-cols-[72px_1fr] lg:grid-cols-[72px_1fr]' : 'md:grid-cols-[72px_1fr] lg:grid-cols-[240px_1fr]',
      )}
    >
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {currentPageLabel(location.pathname)}
      </div>

      <header className="fixed inset-x-0 top-0 z-30 border-b border-line bg-panel/95 pt-[env(safe-area-inset-top)] backdrop-blur-md md:hidden">
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
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-paper"
            aria-label="搜索页面"
          >
            <Search size={18} />
          </button>
        </div>
      </header>

      <div
        className={cn(
          'fixed inset-0 z-40 bg-black/60 transition-opacity duration-300 md:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />

      <aside
        aria-hidden={!isDesktop && !open ? true : undefined}
        inert={!isDesktop && !open ? true : undefined}
        className={cn(
          'safe-area-drawer fixed inset-y-0 left-0 z-50 flex w-72 max-w-[80vw] flex-col border-r border-line bg-panel transition-transform duration-300 ease-out',
          'md:static md:z-auto md:h-svh md:w-auto md:max-w-none md:translate-x-0 md:overflow-hidden md:bg-panel/80',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {/* 移动抽屉头部 */}
        <div className="flex items-center gap-2 border-b border-line px-3 py-3 md:hidden">
          <span className={BRAND_TILE} aria-hidden="true">
            PD
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">AI一体化服务平台</div>
            <div className="font-mono text-[10px] tracking-[0.22em] text-mist-soft">PIVOT DESK</div>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-mist hover:text-paper"
            aria-label="关闭菜单"
          >
            <X size={20} />
          </button>
        </div>

        {/* 桌面/平板品牌区 */}
        <div className={cn('hidden shrink-0 border-b border-line md:block', collapsed ? 'lg:px-3 lg:py-3' : 'px-3 py-3')}>
          <div className={cn('hidden items-center gap-2 lg:flex', collapsed && 'lg:hidden')}>
            <span className={BRAND_TILE} aria-hidden="true">
              PD
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">AI一体化服务平台</div>
              <div className="font-mono text-[10px] tracking-[0.22em] text-mist-soft">PIVOT DESK</div>
            </div>
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-mist-soft transition hover:bg-white/5 hover:text-paper"
              aria-label="折叠侧栏"
              title="折叠侧栏"
            >
              <PanelLeftClose size={16} aria-hidden="true" />
            </button>
          </div>
          <button
            type="button"
            onClick={() => {
              if (collapsed) setCollapsed(false)
            }}
            aria-label={collapsed ? '展开侧栏' : 'PIVOT DESK'}
            title={collapsed ? '展开侧栏' : 'PIVOT DESK'}
            className={cn(
              'mx-auto flex h-10 w-10 items-center justify-center rounded-lg border border-signal/30 bg-signal/10 font-mono text-[11px] tracking-[0.18em] text-signal transition hover:border-signal/60 hover:bg-signal/15',
              collapsed ? 'lg:flex' : 'lg:hidden',
            )}
          >
            PD
          </button>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className={cn(
              'mt-3 hidden w-full items-center gap-2 rounded-lg border border-line bg-ink/60 px-2.5 py-2 text-left text-xs text-mist-soft transition hover:border-line-strong hover:text-paper lg:flex',
              collapsed && 'lg:hidden',
            )}
            aria-label="搜索页面（⌘K）"
          >
            <Search size={14} aria-hidden="true" />
            <span className="flex-1">搜索页面</span>
            <kbd className="rounded border border-line px-1 py-0.5 font-mono text-[10px]">⌘K</kbd>
          </button>
        </div>

        {/* 移动抽屉搜索 */}
        <div className="px-3 pt-3 md:hidden">
          <div className="relative">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" aria-hidden="true" />
            <Input
              value={drawerQuery}
              onChange={(event) => setDrawerQuery(event.target.value)}
              placeholder="搜索功能"
              aria-label="搜索功能"
              className="pl-9"
            />
          </div>
        </div>

        <nav
          className="nav-rail flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-3 py-2"
          aria-label="主导航"
        >
          {visibleGroups.map((group, index) => {
            // 搜索时强制展开；图标轨（md）没有分组标题，也必须让所有入口可见
            const groupClosed = !normalizedQuery && closedGroups.has(group.label)
            return (
              <div
                key={group.label}
                data-active-group={activeGroup === group.label ? 'true' : undefined}
                className="flex flex-col"
              >
                <button
                  type="button"
                  onClick={() => toggleGroup(group.label)}
                  aria-expanded={!groupClosed}
                  className={cn(
                    'flex items-center justify-between gap-2 rounded-md px-2.5 pb-1 pt-3 text-[11px] uppercase tracking-[0.16em] text-mist-soft transition hover:text-paper md:hidden',
                    collapsed ? 'lg:hidden' : 'lg:flex',
                  )}
                >
                  <span>{group.label}</span>
                  <ChevronDown
                    size={12}
                    className={cn('shrink-0 transition-transform', groupClosed && '-rotate-90')}
                    aria-hidden="true"
                  />
                </button>
                {index > 0 ? (
                  <div
                    className={cn('mx-3 my-2.5 hidden border-t border-line/70 md:block', collapsed ? 'lg:block' : 'lg:hidden')}
                    aria-hidden="true"
                  />
                ) : null}
                <div
                  className={cn(
                    'flex flex-col gap-1',
                    groupClosed
                      ? collapsed
                        ? 'hidden md:flex'
                        : 'hidden md:flex lg:hidden'
                      : 'flex',
                  )}
                >
                  {group.links.map((link) => (
                    <NavLink
                      key={link.to}
                      to={link.to}
                      end={link.to === '/' || link.to === '/benchmark'}
                      onClick={() => setOpen(false)}
                      title={link.label}
                      aria-label={link.label}
                      className={({ isActive }) =>
                        cn(
                          'relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors md:mx-auto md:h-10 md:w-10 md:justify-center md:p-0',
                          collapsed
                            ? 'lg:h-10 lg:w-10 lg:justify-center lg:p-0'
                            : 'lg:mx-0 lg:h-9 lg:w-auto lg:justify-start lg:px-2.5 lg:py-0',
                          isActive
                            ? 'bg-signal/15 text-signal ring-1 ring-inset ring-signal/25'
                            : 'text-mist hover:bg-white/[0.06] hover:text-paper',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive && !collapsed ? (
                            <span
                              className="absolute left-0 top-1/2 hidden h-4 w-0.5 -translate-y-1/2 rounded-full bg-signal lg:block"
                              aria-hidden="true"
                            />
                          ) : null}
                          <link.icon
                            size={18}
                            className={cn(
                              'h-[18px] w-[18px] shrink-0 md:h-5 md:w-5',
                              !collapsed && 'lg:h-[18px] lg:w-[18px]',
                            )}
                            aria-hidden="true"
                          />
                          <span className={cn('truncate md:hidden', collapsed ? 'lg:hidden' : 'lg:inline')}>
                            {link.label}
                          </span>
                        </>
                      )}
                    </NavLink>
                  ))}
                </div>
              </div>
            )
          })}
          {!visibleGroups.length ? <div className="px-3 py-6 text-center text-sm text-mist">没有匹配的功能</div> : null}
        </nav>

        <div
          className={cn(
            'flex shrink-0 items-center justify-between gap-1 border-t border-line bg-ink/40 px-3 py-2 md:flex-col md:items-center md:justify-center lg:border-t-0',
            collapsed ? 'lg:flex-col lg:justify-center' : 'lg:flex-row lg:justify-between',
          )}
        >
          <ForceUpdateButton className="h-10 w-10 shrink-0 justify-center p-0 text-mist" title="强制更新">
            <span className="sr-only">强制更新</span>
          </ForceUpdateButton>
          <Button
            variant="ghost"
            className="h-10 w-10 shrink-0 justify-center p-0 text-mist"
            onClick={() => setDensity((value) => (value === 'compact' ? 'comfortable' : 'compact'))}
            title={density === 'compact' ? '密度：紧凑（点击切换舒适）' : '密度：舒适（点击切换紧凑）'}
            aria-label={density === 'compact' ? '切换为舒适密度' : '切换为紧凑密度'}
            aria-pressed={density === 'compact'}
          >
            <Rows3 size={16} />
          </Button>
          <Button
            variant="ghost"
            className="hidden h-10 w-10 shrink-0 justify-center p-0 text-mist md:inline-flex lg:hidden"
            onClick={() => setPaletteOpen(true)}
            title="搜索页面（⌘K）"
            aria-label="搜索页面"
          >
            <Search size={16} />
          </Button>
          <Button
            variant="ghost"
            className="hidden h-10 w-10 shrink-0 justify-center p-0 text-mist md:inline-flex"
            onClick={() => setCollapsed((value) => !value)}
            title={collapsed ? '展开侧栏' : '折叠侧栏'}
            aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
            aria-expanded={!collapsed}
          >
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </Button>
          <Button
            variant="ghost"
            className="h-10 w-10 shrink-0 justify-center p-0 text-mist"
            onClick={() => setProfile(true)}
            title="修改账号"
            aria-label="修改账号"
          >
            <UserRoundPen size={16} />
          </Button>
          <Button
            variant="ghost"
            className="h-10 w-10 shrink-0 justify-center p-0 text-mist"
            onClick={logout}
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut size={16} />
          </Button>
        </div>
      </aside>
      {profile ? <ProfileDialog onClose={() => setProfile(false)} /> : null}

      <main
        ref={mainRef}
        className="bg-ink px-4 pb-[calc(var(--app-tab)+env(safe-area-inset-bottom)+0.75rem)] pt-[calc(var(--app-header)+env(safe-area-inset-top)+0.75rem)] md:h-svh md:overflow-y-auto md:px-6 md:pb-6 md:pt-6 lg:px-8"
      >
        <PageContainer>
          <Outlet />
        </PageContainer>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden">
        <div className="flex h-14">
          {tabLinks.map((link) => {
            const active = isPathActive(link.to, location.pathname)
            return (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                onClick={() => setOpen(false)}
                className={cn(
                  'flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 text-xs leading-none',
                  active ? 'text-signal' : 'text-mist',
                )}
              >
                <link.icon size={18} aria-hidden="true" />
                {link.label}
              </NavLink>
            )
          })}
        </div>
      </nav>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  )
}
