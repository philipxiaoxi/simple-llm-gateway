import { History, Loader2, Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button, Input } from '../ui'
import { cn } from '../../lib/utils'

export function Spinner({ className }: { className?: string }) {
  return <Loader2 size={15} className={cn('animate-spin', className)} />
}

export function HistoryInput({
  value,
  onChange,
  onSubmit,
  placeholder,
  history,
  busy,
  submitLabel = '解析',
  listId,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder: string
  history: string[]
  busy: boolean
  submitLabel?: string
  listId: string
}) {
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row"
      onSubmit={(event) => {
        event.preventDefault()
        if (!busy && value.trim()) onSubmit()
      }}
    >
      <div className="relative min-w-0 flex-1">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
        <Input
          value={value}
          list={history.length ? listId : undefined}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          className="pl-9"
        />
        {history.length ? (
          <datalist id={listId}>
            {history.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        ) : null}
        {history.length ? (
          <History size={13} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-mist/50" />
        ) : null}
      </div>
      <Button type="submit" disabled={busy || !value.trim()} className="shrink-0">
        {busy ? <Spinner /> : null}
        {submitLabel}
      </Button>
    </form>
  )
}

export function ResultCard({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string
  subtitle?: string
  children?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="rounded-xl border border-line bg-panel-2/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-paper">{title}</div>
          {subtitle ? <div className="mt-0.5 truncate text-xs text-mist">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children ? <div className="mt-3">{children}</div> : null}
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
      {message}
    </div>
  )
}

export function EmptyNote({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-sm text-mist">
      {message}
    </div>
  )
}
