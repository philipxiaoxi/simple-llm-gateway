import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { api, type Me } from './api'
import { Button } from '../components/ui'
import { NAV_GROUPS, type NavLink } from '../components/nav'

/**
 * 登录会话与功能权限。
 *
 * - 启动时拉一次 `/api/admin/me`，得到当前账号的角色与权限码列表；
 * - `can(code)` 是唯一的判定入口：内置管理员（permissions 含 `*`）恒为真，
 *   否则按权限码匹配，并支持 `resource:*` 通配；
 * - 页面只声明权限码（`<Can code="accounts:delete">` / `useCan('logs:export')`），
 *   不重复写权限判断逻辑。
 */
type SessionValue = {
  me?: Me
  isLoading: boolean
  roleName: string
  can: (code?: string) => boolean
}

const SessionContext = createContext<SessionValue>({
  isLoading: true,
  roleName: '',
  can: () => false,
})

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data, isPending } = useQuery({
    queryKey: ['session', 'me'],
    queryFn: api.me,
    staleTime: 30_000,
    retry: false,
  })

  const value = useMemo<SessionValue>(() => {
    const permissions = data?.permissions ?? []
    const wildcard = permissions.includes('*') || data?.role?.builtin === true
    const can = (code?: string) => {
      if (!code) return true
      if (wildcard) return true
      if (permissions.includes(code)) return true
      const [resource] = code.split(':')
      return permissions.includes(`${resource}:*`)
    }
    return { me: data, isLoading: isPending, roleName: data?.role?.name ?? '', can }
  }, [data, isPending])

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  return useContext(SessionContext)
}

export function useCan(code?: string): boolean {
  return useSession().can(code)
}

/** 无权限时不渲染（按钮、菜单项、行内操作都用它）。 */
export function Can({
  code,
  children,
  fallback = null,
}: {
  code: string
  children: ReactNode
  fallback?: ReactNode
}) {
  const { can, isLoading } = useSession()
  if (isLoading) return null
  return can(code) ? <>{children}</> : <>{fallback}</>
}

export function ForbiddenPage() {
  const { roleName, can } = useSession()
  const navigate = useNavigate()
  const fallback = useMemo<NavLink | null>(() => {
    for (const group of NAV_GROUPS) {
      for (const link of group.links) {
        if (can(link.perm)) return link
      }
    }
    return null
  }, [can])
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="text-lg font-medium text-paper">没有访问权限</div>
      <p className="max-w-md text-sm text-mist">
        当前角色（{roleName || '未分配'}）没有这个页面的权限，请联系管理员在「角色管理」中调整。
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button variant="line" onClick={() => navigate(-1)}>
          返回上一页
        </Button>
        {fallback ? (
          <Button onClick={() => navigate(fallback.to)}>前往「{fallback.label}」</Button>
        ) : (
          <Button onClick={() => navigate('/login')}>重新登录</Button>
        )}
      </div>
    </div>
  )
}

/** 页面级路由守卫：无权限时展示无权限页，避免直接输 URL 进入。 */
export function RequirePerm({ code, children }: { code: string; children: ReactNode }) {
  const { can, isLoading } = useSession()
  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-[3px] border-signal/20 border-t-signal" />
      </div>
    )
  }
  return can(code) ? <>{children}</> : <ForbiddenPage />
}
