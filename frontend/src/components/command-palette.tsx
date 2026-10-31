import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CornerDownLeft, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { cn } from '../lib/utils'
import { NAV_GROUPS, SEARCH_LINKS, filterNavGroups, filterNavLinks } from './nav'
import { useSession } from '../lib/session'

/**
 * PC 端命令面板（⌘K / Ctrl+K）：按名称、路径或关键词跳转页面。
 *
 * 只做导航，不承载危险操作，避免「输入即执行」的误触。
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const { can } = useSession()
  const navGroups = useMemo(() => filterNavGroups(NAV_GROUPS, can), [can])
  const navLinks = useMemo(() => filterNavLinks(SEARCH_LINKS, can), [can])
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return navLinks
    return navLinks.filter((link) =>
      `${link.label} ${link.to} ${link.keywords ?? ''}`.toLowerCase().includes(q),
    )
  }, [query, navLinks])

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
    inputRef.current?.focus()
    const body = document.body
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previous
    }
  }, [open])

  useEffect(() => {
    setActive(0)
  }, [query])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((value) => Math.min(results.length - 1, value + 1))
      } else if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((value) => Math.max(0, value - 1))
      } else if (event.key === 'Enter') {
        const target = results[active]
        if (!target) return
        event.preventDefault()
        navigate(target.to)
        onClose()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [open, results, active, navigate, onClose])

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const groupOf = (to: string) => {
    const group = navGroups.find((item) => item.links.some((link) => link.to === to))
    if (group) return group.label
    return to.startsWith('/mcp-plaza') ? 'MCP 广场' : ''
  }

  return createPortal(
    // 遮罩点击关闭只是辅助快捷方式，键盘用户用 Esc / Enter
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 px-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="跳转页面"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-line-strong bg-panel shadow-[0_24px_70px_rgba(0,0,0,0.55)]"
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          <Search size={16} className="shrink-0 text-mist" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索页面，回车跳转"
            aria-label="搜索页面"
            className="h-12 w-full bg-transparent text-base text-paper outline-none placeholder:text-mist-soft"
          />
          <kbd className="hidden shrink-0 rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-mist sm:block">
            Esc
          </kbd>
        </div>
        <div ref={listRef} className="max-h-[52vh] overflow-y-auto overscroll-contain py-2" role="listbox" aria-label="页面列表">
          {results.length ? (
            results.map((link, index) => (
              <button
                key={link.to}
                type="button"
                role="option"
                aria-selected={index === active}
                data-index={index}
                onMouseEnter={() => setActive(index)}
                onClick={() => {
                  navigate(link.to)
                  onClose()
                }}
                className={cn(
                  'flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition',
                  index === active ? 'bg-signal/15 text-signal' : 'text-paper hover:bg-white/5',
                )}
              >
                <link.icon size={16} className="shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{link.label}</span>
                <span className="shrink-0 text-[11px] text-mist-soft">{groupOf(link.to)}</span>
                {index === active ? <CornerDownLeft size={14} className="shrink-0" aria-hidden="true" /> : null}
              </button>
            ))
          ) : (
            <div className="px-3 py-6 text-center text-sm text-mist">没有匹配的页面</div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
