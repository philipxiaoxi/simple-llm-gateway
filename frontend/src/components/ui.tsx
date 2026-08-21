import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { cn } from '../lib/utils'

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
}: {
  title: string
  children: ReactNode
  onClose: () => void
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
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60">
      <div className="mx-auto my-[15vh] w-[calc(100%-2rem)] max-w-lg rounded-xl border border-line bg-panel p-5 shadow-[0_20px_60px_rgba(0,0,0,0.45)]">
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
