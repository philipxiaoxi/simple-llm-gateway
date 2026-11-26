import { NavLink } from 'react-router-dom'
import type { McpCatalogItem } from '../lib/api'
import { cn } from '../lib/utils'
import { Button } from './ui'

export function McpCatalogCard({ item, onOpen }: { item: McpCatalogItem; onOpen?: () => void }) {
  const external = item.source === 'external'
  const to = item.admin_path || undefined
  const className = cn(
    'flex min-h-[11.5rem] min-w-0 flex-col items-stretch justify-start gap-0 rounded-none border border-line bg-panel px-5 py-5 text-left font-normal md:min-h-[11.5rem]',
    (onOpen || to) && 'cursor-pointer transition hover:-translate-y-0.5 hover:border-signal/70 hover:bg-panel hover:shadow-glow active:scale-100',
    item.status === 'offline' && 'opacity-60',
    item.status === 'offline' && (onOpen || to) && 'hover:opacity-100',
  )
  const body = (
    <>
      <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-signal">
        MCP / {external ? `外部 SDK · ${item.status === 'enabled' ? '在线' : '离线'}` : `${item.category || '能力'} / 能力`}
      </span>
      <span className="mt-3 break-words text-xl font-semibold tracking-tight text-paper [overflow-wrap:anywhere]">{item.name}</span>
      <span className="mt-2 line-clamp-3 text-sm leading-6 text-mist [overflow-wrap:anywhere]">{item.description}</span>
      <span className="mt-auto break-words pt-4 text-[11px] text-mist-soft [overflow-wrap:anywhere]">
        {item.capability_id} · v{item.version} · {item.tools.length} 个工具
        {!to && !external ? ' · 协议面' : ''}
      </span>
    </>
  )
  if (onOpen) return <Button type="button" variant="ghost" aria-haspopup="dialog" onClick={onOpen} className={className}>{body}</Button>
  if (to) return <NavLink to={to} className={className}>{body}</NavLink>
  return <div className={className}>{body}</div>
}
