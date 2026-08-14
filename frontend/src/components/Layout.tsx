import { Activity, KeyRound, Menu, MessageSquareText, RadioTower, X } from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearToken } from '../lib/api'
import { cn } from '../lib/utils'
import { Button } from './ui'

const links = [
  { to: '/', label: '概览', icon: Activity },
  { to: '/accounts', label: '上游账号', icon: RadioTower },
  { to: '/keys', label: 'API Key', icon: KeyRound },
  { to: '/logs', label: '记录审计', icon: MessageSquareText },
]

export function Layout() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  function logout() {
    clearToken()
    navigate('/login')
  }

  return (
    <div className="min-h-svh lg:h-svh lg:overflow-hidden lg:grid lg:grid-cols-[240px_1fr]">
      <header className="flex items-center justify-between border-b border-line px-4 py-3 lg:hidden">
        <div className="font-mono text-sm tracking-[0.2em] text-signal">中转台</div>
        <button onClick={() => setOpen((value) => !value)} className="text-paper">
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
      </header>
      <aside
        className={cn(
          'border-line bg-panel/80 lg:flex lg:h-svh lg:flex-col lg:border-r lg:overflow-hidden',
          open ? 'block' : 'hidden lg:flex',
        )}
      >
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
                  'flex items-center gap-2 rounded-md px-3 py-2 text-sm',
                  isActive ? 'bg-signal/15 text-signal' : 'text-mist hover:bg-white/5 hover:text-paper',
                )
              }
            >
              <link.icon size={16} />
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3">
          <Button variant="ghost" className="w-full justify-start text-mist" onClick={logout}>
            退出登录
          </Button>
        </div>
      </aside>
      <main className="px-4 py-6 lg:h-svh lg:overflow-y-auto lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
