import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, KeyRound, Menu, MessageSquareText, RadioTower, ServerCog, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, clearToken, setToken } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { MIN_PASSWORD_LENGTH, cn, errorMessage } from '../lib/utils'
import { Button, Dialog, Field, Input } from './ui'

const links = [
  { to: '/', label: '概览', icon: Activity },
  { to: '/accounts', label: '上游账号', icon: RadioTower },
  { to: '/agents', label: '网关 Agent', icon: ServerCog },
  { to: '/keys', label: 'API Key', icon: KeyRound },
  { to: '/logs', label: '记录审计', icon: MessageSquareText },
]

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

export function Layout() {
  const [open, setOpen] = useState(false)
  const [profile, setProfile] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const location = useLocation()

  function logout() {
    clearToken()
    void queryClient.clear()
    navigate('/login')
  }

  return (
    <div className="min-h-svh lg:h-svh lg:overflow-hidden lg:grid lg:grid-cols-[240px_1fr]">
      <header className="flex items-center justify-between border-b border-line px-4 py-3 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex min-h-11 min-w-11 items-center justify-center text-paper"
          aria-label="打开菜单"
        >
          <Menu size={20} />
        </button>
        <div className="font-mono text-sm tracking-[0.2em] text-signal">中转台</div>
        <div className="min-w-11" aria-hidden="true" />
      </header>

      {/* 移动端：侧拉抽屉遮罩 */}
      <div
        className={cn(
          'fixed inset-0 z-40 bg-black/60 transition-opacity duration-300 lg:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />

      {/* 移动端：侧拉抽屉；桌面端：固定侧边栏 */}
      <aside
        className={cn(
          'safe-area-drawer fixed inset-y-0 left-0 z-50 flex w-72 max-w-[80vw] flex-col border-r border-line bg-panel transition-transform duration-300 ease-out lg:static lg:z-auto lg:h-svh lg:w-auto lg:max-w-none lg:translate-x-0 lg:overflow-hidden lg:border-r lg:bg-panel/80',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-4 lg:hidden">
          <div className="font-mono text-xs tracking-[0.28em] text-signal">SIGNAL DESK</div>
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
          <div className="font-mono text-xs tracking-[0.28em] text-signal">SIGNAL DESK</div>
          <div className="mt-1 text-lg font-semibold">中转台</div>
        </div>
        <nav className="flex flex-col gap-1 p-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === '/'}
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
        <div className="flex flex-col gap-1 p-3">
          <Button variant="ghost" className="w-full justify-start text-mist" onClick={() => setProfile(true)}>
            修改账号
          </Button>
          <Button variant="ghost" className="w-full justify-start text-mist" onClick={logout}>
            退出登录
          </Button>
        </div>
      </aside>
      {profile ? <ProfileDialog onClose={() => setProfile(false)} /> : null}
      <main className="px-4 py-6 lg:h-svh lg:overflow-y-auto lg:px-8">
        <div key={location.pathname} className="page-enter">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
