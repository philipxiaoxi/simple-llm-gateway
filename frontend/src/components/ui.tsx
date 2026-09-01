import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { cn } from '../lib/utils'

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  onLabel = '开启',
  offLabel = '关闭',
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  onLabel?: string
  offLabel?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'inline-flex min-h-9 shrink-0 items-center gap-2 rounded-full border px-2 py-1 text-[11px] font-medium transition md:min-h-7',
        checked
          ? 'border-signal/35 bg-signal/10 text-signal hover:bg-signal/15'
          : 'border-line bg-white/[0.03] text-mist hover:border-mist/40 hover:text-paper',
        disabled && 'cursor-not-allowed opacity-40',
      )}
    >
      <span
        className={cn(
          'relative inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors',
          checked ? 'justify-end bg-signal' : 'justify-start bg-white/20',
        )}
      >
        <span className="h-3 w-3 rounded-full bg-white shadow-sm" />
      </span>
      {checked ? onLabel : offLabel}
    </button>
  )
}

export function Button({
  className,
  variant = 'primary',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' | 'line' }) {
  return (
    <button
      className={cn(
        'inline-flex min-h-11 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:opacity-40 md:min-h-9',
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
        'w-full rounded-md border border-line bg-ink px-3 py-2 text-base text-paper outline-none placeholder:text-mist/70 focus:border-signal/70 md:text-sm',
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
  title,
}: {
  children: ReactNode
  tone?: 'ok' | 'bad' | 'warn' | 'mist' | 'info'
  title?: string
}) {
  const map = {
    ok: 'bg-signal/15 text-signal',
    bad: 'bg-danger/15 text-danger',
    warn: 'bg-warn/15 text-warn',
    mist: 'bg-white/5 text-mist',
    info: 'bg-info/15 text-info',
  }
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', map[tone])}>
      {children}
    </span>
  )
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        'w-full rounded-md border border-line bg-ink px-3 py-2 text-base text-paper outline-none focus:border-signal/70 md:text-sm',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs uppercase tracking-[0.16em] text-mist">{label}</span>
      {children}
    </label>
  )
}

export function Dialog({
  title,
  children,
  onClose,
  className,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  className?: string
}) {
  // 弹窗打开时锁定背景滚动，关闭时恢复
  useEffect(() => {
    const body = document.body
    const previousOverflow = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previousOverflow
    }
  }, [])

  // 挂载到 body，避免被祖先的 transform/filter 等属性限制（fixed 定位失效）
  return createPortal(
    <div className="safe-area-modal fixed inset-0 z-50 overflow-y-auto bg-black/60">
      <div className={cn('mx-auto my-[15vh] w-full max-w-lg rounded-xl border border-line bg-panel p-5 shadow-[0_20px_60px_rgba(0,0,0,0.45)]', className)}>
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            type="button"
            className="inline-flex min-h-11 min-w-11 items-center justify-center text-mist hover:text-paper"
            onClick={onClose}
          >
            关闭
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
