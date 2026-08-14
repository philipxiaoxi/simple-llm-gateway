import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { cn } from '../lib/utils'

export function Button({
  className,
  variant = 'primary',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' | 'line' }) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:opacity-40',
        variant === 'primary' && 'bg-signal text-ink hover:brightness-110',
        variant === 'ghost' && 'bg-transparent text-paper hover:bg-white/5',
        variant === 'danger' && 'bg-danger/15 text-danger hover:bg-danger/25',
        variant === 'line' && 'border border-line bg-panel-2 text-paper hover:border-mist/40',
        className,
      )}
      {...props}
    />
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none placeholder:text-mist/70 focus:border-signal/70',
        className,
      )}
      {...props}
    />
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('rounded-xl border border-line bg-panel/90 p-4 shadow-[0_10px_40px_rgba(0,0,0,0.25)]', className)}>{children}</div>
}

export function Badge({
  children,
  tone = 'mist',
}: {
  children: ReactNode
  tone?: 'ok' | 'bad' | 'warn' | 'mist' | 'info'
}) {
  const map = {
    ok: 'bg-signal/15 text-signal',
    bad: 'bg-danger/15 text-danger',
    warn: 'bg-warn/15 text-warn',
    mist: 'bg-white/5 text-mist',
    info: 'bg-info/15 text-info',
  }
  return <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', map[tone])}>{children}</span>
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs uppercase tracking-[0.16em] text-mist">{label}</span>
      {children}
    </label>
  )
}
