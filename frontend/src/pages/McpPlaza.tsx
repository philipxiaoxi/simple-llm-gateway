import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  Download,
  FileText,
  Globe,
  Image as ImageIcon,
  KeyRound,
  Layers3,
  Newspaper,
  ScrollText,
  Shapes,
  Search,
  Shirt,
  Sparkles,
  Store,
  UserRound,
  X,
} from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { cn, errorMessage } from '../lib/utils'
import { useSession } from '../lib/session'
import { PageHeader } from '../components/layout-primitives'
import { Input } from '../components/ui'

const tabs = [
  { to: '/mcp-plaza', label: '服务目录', icon: Store, end: true, perm: 'mcp_catalog:view' },
  { to: '/mcp-plaza/keys', label: 'MCP Key', icon: KeyRound, perm: 'mcp_keys:view' },
  { to: '/mcp-plaza/calls', label: '调用记录', icon: ScrollText, perm: 'mcp_calls:view' },
  { to: '/mcp-plaza/docs', label: '接入说明', icon: Layers3, perm: 'mcp_catalog:view' },
]

const APP_PATH_PERMS: { prefix: string; perm: string }[] = [
  { prefix: '/mcp-plaza/knowledge', perm: 'mcp_knowledge:view' },
  { prefix: '/mcp-plaza/docparse', perm: 'mcp_docparse:view' },
  { prefix: '/mcp-plaza/sites', perm: 'mcp_sites:view' },
  { prefix: '/mcp-plaza/douyin', perm: 'mcp_douyin:view' },
  { prefix: '/mcp-plaza/portraits', perm: 'mcp_portraits:view' },
  { prefix: '/mcp-plaza/poses', perm: 'mcp_poses:view' },
  { prefix: '/mcp-plaza/costumes', perm: 'mcp_costumes:view' },
  { prefix: '/mcp-plaza/diagrams', perm: 'mcp_diagrams:view' },
  { prefix: '/mcp-plaza/generation', perm: 'generation:settings' },
  { prefix: '/mcp-plaza/keys', perm: 'mcp_keys:view' },
  { prefix: '/mcp-plaza/info-report', perm: 'mcp_info:view' },
  { prefix: '/info', perm: 'info:view' },
]

function permForAppPath(path: string | null | undefined): string {
  if (!path) return 'mcp_catalog:view'
  const match = [...APP_PATH_PERMS].sort((a, b) => b.prefix.length - a.prefix.length).find((item) =>
    path === item.prefix || path.startsWith(`${item.prefix}/`),
  )
  return match?.perm ?? 'mcp_catalog:view'
}

const iconMap: Record<string, typeof BookOpen> = {
  book: BookOpen,
  store: Store,
  'file-text': FileText,
  globe: Globe,
  download: Download,
  shapes: Shapes,
  newspaper: Newspaper,
  sparkles: Sparkles,
  image: ImageIcon,
  user: UserRound,
  shirt: Shirt,
}

export function McpPlazaLayout() {
  const { can } = useSession()
  const visibleTabs = tabs.filter((tab) => can(tab.perm))
  const location = useLocation()
  const platformPaths = ['/mcp-plaza/keys', '/mcp-plaza/calls', '/mcp-plaza/docs']
  const inApp =
    location.pathname.startsWith('/mcp-plaza/') &&
    location.pathname !== '/mcp-plaza' &&
    !platformPaths.some((path) => location.pathname === path || location.pathname.startsWith(`${path}/`))

  return (
    <div className="space-y-4 md:space-y-5">
      <PageHeader
        title="MCP 广场"
        description={
          inApp
            ? '应用管理页。平台入口：服务目录、MCP Key、调用记录、接入说明。'
            : '从卡片进入应用。MCP Key 与调用审计是平台级能力。'
        }
      />
      {inApp ? null : (
        <div className="-mx-4 flex gap-2 overflow-x-auto border-b border-line px-4 pb-2 md:mx-0 md:px-0 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {visibleTabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                cn(
                  'inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-2 text-sm md:min-h-0',
                  isActive ? 'bg-signal/15 text-signal' : 'text-mist hover:text-paper',
                )
              }
            >
              <tab.icon size={15} />
              {tab.label}
            </NavLink>
          ))}
        </div>
      )}
      <Outlet />
    </div>
  )
}

export function McpPlazaCatalogPage() {
  const { can } = useSession()
  const { data, isError, error, isFetching } = useQuery({
    queryKey: ['mcp-catalog', 'plaza'],
    queryFn: () => api.mcpCatalog('plaza'),
  })
  const [keyword, setKeyword] = useState('')
  const query = keyword.trim().toLowerCase()
  const items = useMemo(() => {
    const visible = (data?.items || []).filter((item) => {
      if (item.status !== 'enabled') return false
      if (!item.admin_path) return true
      // 优先用后端下发的各能力权限；旧后端没有该字段时回退到按路径推导
      if (item.permissions?.length) return item.permissions.some((code) => can(code))
      return can(permForAppPath(item.admin_path))
    })
    if (!query) return visible
    return visible.filter((item) =>
      [item.name, item.capability_id, item.description, item.category, ...item.tools.map((tool) => tool.name)]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query),
    )
  }, [can, data?.items, query])

  return (
    <div className="space-y-4">
      {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载目录失败')}</div> : null}
      <div className="relative">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
        <Input
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索应用名、id、描述或工具名"
          aria-label="搜索服务目录"
          className="min-h-11 pl-9 pr-10"
        />
        {keyword ? (
          <button
            type="button"
            onClick={() => setKeyword('')}
            aria-label="清空搜索"
            className="absolute right-2 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-mist hover:text-paper"
          >
            <X size={14} />
          </button>
        ) : null}
      </div>
      {query && items.length ? <div className="text-xs text-mist">找到 {items.length} 个应用</div> : null}
      {isFetching && !data ? <div className="text-sm text-mist">加载中…</div> : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
        {items.map((item) => {
          const Icon = iconMap[item.icon] || Store
          const to = item.admin_path || undefined
          const className = "flex min-h-[8.5rem] flex-col rounded-xl border border-line bg-panel p-4 transition hover:border-signal/40 hover:bg-panel/80 sm:min-h-[9.5rem] sm:p-5"
          const body = (
            <>
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-signal/10 text-signal">
                    <Icon size={17} />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-base font-medium text-paper sm:text-lg">{item.name}</div>
                    <div className="mt-0.5 text-[11px] text-mist">
                      {item.capability_id} · v{item.version} · {item.tools.length} tools
                    </div>
                  </div>
                </div>
                {to ? <span className="shrink-0 pt-1 text-sm text-signal">进入 →</span> : null}
              </div>
              <p className="mt-3 line-clamp-2 text-sm leading-6 text-mist">{item.description}</p>
              {!to ? <div className="mt-3 text-xs text-mist">协议面能力（无独立管理页）</div> : null}
            </>
          )
          if (to) {
            return (
              <NavLink key={item.capability_id} to={to} className={className}>
                {body}
              </NavLink>
            )
          }
          return (
            <div key={item.capability_id} className={className}>
              {body}
            </div>
          )
        })}
      </div>
      {!items.length && !isFetching ? (
        <div className="rounded-xl border border-dashed border-line p-10 text-center text-sm text-mist">
          {query ? `没有匹配「${keyword.trim()}」的应用` : '注册表中暂无启用能力。后端在 capabilities 中 register 新 Provider 后会自动出现。'}
        </div>
      ) : null}
    </div>
  )
}

export default McpPlazaLayout
