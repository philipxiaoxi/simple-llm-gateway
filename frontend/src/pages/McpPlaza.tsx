import { useQuery } from '@tanstack/react-query'
import { BookOpen, Download, FileText, Globe, KeyRound, Layers3, Newspaper, ScrollText, Shapes, Sparkles, Store } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { cn, errorMessage } from '../lib/utils'
import { useSession } from '../lib/session'
import { PageHeader } from '../components/layout-primitives'

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
  { prefix: '/mcp-plaza/diagrams', perm: 'mcp_diagrams:view' },
  { prefix: '/mcp-plaza/generation', perm: 'mcp_generation:view' },
  { prefix: '/mcp-plaza/keys', perm: 'mcp_keys:view' },
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
    <div className="space-y-5">
      <PageHeader
        title="MCP 广场"
        description={
          inApp
            ? '应用管理页。平台级入口：服务目录 / MCP Key / 调用记录 / 接入说明。'
            : '服务目录由能力注册表驱动；每个应用独立维护，从卡片进入。MCP Key 与调用审计为平台能力。'
        }
      />
      {inApp ? null : (
        <div className="flex gap-2 overflow-x-auto border-b border-line pb-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {visibleTabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                cn(
                  'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-2 text-sm',
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
    queryKey: ['mcp-catalog'],
    queryFn: () => api.mcpCatalog(),
  })
  const items = (data?.items || []).filter(
    (item) => item.status === 'enabled' && (!item.admin_path || can(permForAppPath(item.admin_path))),
  )

  return (
    <div className="space-y-4">
      {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载目录失败')}</div> : null}
      {isFetching && !items.length ? <div className="text-sm text-mist">加载中…</div> : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => {
          const Icon = iconMap[item.icon] || Store
          const to = item.admin_path || undefined
          const body = (
            <>
              <div className="flex items-center gap-2 text-lg font-medium text-paper">
                <Icon size={18} className="text-signal" />
                {item.name}
              </div>
              <p className="mt-2 text-sm text-mist">{item.description}</p>
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-mist">
                <span className="rounded-full border border-line px-2 py-0.5">id: {item.capability_id}</span>
                <span className="rounded-full border border-line px-2 py-0.5">v{item.version}</span>
                {item.category ? (
                  <span className="rounded-full border border-line px-2 py-0.5">{item.category}</span>
                ) : null}
                <span className="rounded-full border border-line px-2 py-0.5">{item.tools.length} tools</span>
              </div>
              {to ? <div className="mt-4 text-sm text-signal">打开应用 →</div> : (
                <div className="mt-4 text-xs text-mist">协议面能力（无独立管理页）</div>
              )}
            </>
          )
          if (to) {
            return (
              <NavLink
                key={item.capability_id}
                to={to}
                className="rounded-xl border border-line bg-panel p-5 transition hover:border-signal/40 hover:bg-panel/80"
              >
                {body}
              </NavLink>
            )
          }
          return (
            <div key={item.capability_id} className="rounded-xl border border-line bg-panel p-5">
              {body}
            </div>
          )
        })}
      </div>
      {!items.length && !isFetching ? (
        <div className="rounded-xl border border-dashed border-line p-10 text-center text-sm text-mist">
          注册表中暂无启用能力。后端在 capabilities 中 register 新 Provider 后会自动出现。
        </div>
      ) : null}
    </div>
  )
}

export default McpPlazaLayout
