import { ChevronDown, Copy } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { isLongText, type ToolCall } from '../lib/logView'
import { cn, copyText } from '../lib/utils'
import { notifyBad, notifyOk } from '../lib/toast'
import { Badge } from './ui'

export function CopyButton({
  value,
  label = '复制',
  className,
}: {
  value: string
  label?: string
  className?: string
}) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-mist transition hover:bg-white/5 hover:text-paper',
        className,
      )}
      onClick={() => {
        void copyText(value)
          .then(() => notifyOk('已复制'))
          .catch(() => notifyBad('复制失败'))
      }}
    >
      <Copy size={12} />
      {label}
    </button>
  )
}

export function CollapsibleText({
  text,
  className,
  forceExpanded,
}: {
  text: string
  className?: string
  forceExpanded?: boolean
}) {
  const [open, setOpen] = useState(false)
  const long = isLongText(text)
  const expanded = open || Boolean(forceExpanded)
  return (
    <div className={cn('min-w-0', className)}>
      <div
        className={cn(
          'max-w-full overflow-x-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]',
          long && !expanded && 'line-clamp-8 overflow-hidden',
        )}
      >
        {text || '（空）'}
      </div>
      {long ? (
        <button type="button" className="mt-2 text-xs text-signal hover:underline" onClick={() => setOpen((value) => !value)}>
          {expanded ? '收起' : `展开全部（${text.length} 字）`}
        </button>
      ) : null}
    </div>
  )
}

export function ToolCallList({ calls }: { calls: ToolCall[] }) {
  if (calls.length === 0) return null
  return (
    <div className="mt-2 space-y-2">
      {calls.map((call) => (
        <details key={call.id} className="rounded-lg border border-line bg-ink/50 px-3 py-2 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-paper">
            <Badge tone="info">工具</Badge>
            <span className="font-mono">{call.name}</span>
            <ChevronDown size={12} className="ml-auto text-mist" />
          </summary>
          <pre className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap break-all rounded-md bg-black/30 p-2 font-mono text-[11px] text-mist [overflow-wrap:anywhere]">
            {call.arguments || '（无参数）'}
          </pre>
        </details>
      ))}
    </div>
  )
}

export function ReasoningBlock({ items }: { items: { id: string; text: string }[] }) {
  if (items.length === 0) return null
  return (
    <div className="mt-2 space-y-2">
      {items.map((item) => (
        <details key={item.id} className="rounded-lg border border-line/70 bg-signal/[0.06] px-3 py-2 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-signal">
            <Badge tone="warn">思考</Badge>
            <span className="text-mist">{item.text.length} 字</span>
            <ChevronDown size={12} className="ml-auto" />
          </summary>
          <div className="mt-2 whitespace-pre-wrap break-words text-mist [overflow-wrap:anywhere]">{item.text}</div>
        </details>
      ))}
    </div>
  )
}

export function MetaItem({
  label,
  children,
  className,
}: {
  label: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">{label}</div>
      <div className="mt-1 min-w-0 truncate text-sm">{children}</div>
    </div>
  )
}
