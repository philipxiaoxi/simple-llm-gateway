import { useEffect, useId, useRef } from 'react'
import { X } from 'lucide-react'
import { createPortal } from 'react-dom'
import type { ButtonHTMLAttributes, ComponentPropsWithRef, ReactNode, SelectHTMLAttributes } from 'react'
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

export function Input({ className, ...props }: ComponentPropsWithRef<'input'>) {
  return (
    <input
      className={cn(
        'w-full rounded-md border border-line-strong bg-control px-3 py-2 text-base text-paper outline-none transition-colors placeholder:text-mist-soft focus:border-signal focus:ring-2 focus:ring-signal/20 md:text-sm',
        className,
      )}
      {...props}
    />
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('card-pad rounded-xl border border-line bg-panel/90 shadow-[0_10px_40px_rgba(0,0,0,0.25)]', className)}>{children}</div>
}

export function Badge({
  children,
  tone = 'mist',
  title,
  className,
}: {
  children: ReactNode
  tone?: 'ok' | 'bad' | 'warn' | 'mist' | 'info'
  title?: string
  className?: string
}) {
  const map = {
    ok: 'bg-signal/15 text-signal',
    bad: 'bg-danger/15 text-danger',
    warn: 'bg-warn/15 text-warn',
    mist: 'bg-white/5 text-mist',
    info: 'bg-info/15 text-info',
  }
  return (
    <span
      title={title}
      className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', map[tone], className)}
    >
      {children}
    </span>
  )
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        'w-full rounded-md border border-line-strong bg-control px-3 py-2 text-base text-paper outline-none transition-colors focus:border-signal focus:ring-2 focus:ring-signal/20 md:text-sm',
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

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

const DESKTOP_QUERY = '(min-width: 1024px)'

/**
 * 双端弹层：移动端底部抽屉 / 桌面居中弹窗。
 *
 * 相比旧的 Dialog，补齐了：role/aria-modal、焦点陷阱、Esc 关闭、关闭后焦点归还、
 * 内部滚动（标题与底部操作区固定）、移动端安全区。
 */
export function Sheet({
  title,
  children,
  footer,
  onClose,
  size = 'lg:max-w-lg',
  bodyClassName,
  className,
  mobileFull = false,
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  /** 桌面端最大宽度类名，例如 lg:max-w-2xl */
  size?: string
  bodyClassName?: string
  className?: string
  /** 移动端是否占满视口（长表单用） */
  mobileFull?: boolean
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const headingId = useId()
  // 调用点普遍传内联 onClose（每次渲染都是新函数），用 ref 持有避免 effect 反复重跑
  // 导致焦点被抢回面板（输入框会失焦）。
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      // 焦点陷阱：Tab 在弹层内循环，不允许跑到底层页面
      const nodes = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (node) => node.offsetParent !== null || node === document.activeElement,
      )
      if (!nodes.length) return
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      }
    }

    document.addEventListener('keydown', onKey, true)
    const body = document.body
    const previousOverflow = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      body.style.overflow = previousOverflow
      previous?.focus?.()
    }
  }, [])

  // 挂载到 body，避免被祖先的 transform/filter 等属性限制（fixed 定位失效）
  return createPortal(
    // 遮罩点击只是桌面端的关闭快捷方式；键盘用户用 Esc 或关闭按钮
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 lg:items-center lg:px-4"
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget) return
        // 桌面点遮罩关闭；移动端只允许按钮关闭，避免误触丢掉已填内容
        if (window.matchMedia(DESKTOP_QUERY).matches) onCloseRef.current()
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        className={cn(
          'flex max-h-[90dvh] w-full flex-col overflow-hidden border-line bg-panel outline-none',
          // 移动：底部抽屉（长表单可直接占满视口）
          mobileFull ? 'h-[100dvh] max-h-[100dvh] rounded-none border-0' : 'rounded-t-2xl border-t',
          'pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-[0_-20px_60px_rgba(0,0,0,0.45)]',
          // 桌面：居中弹窗
          'lg:my-[12vh] lg:max-h-[min(84dvh,56rem)] lg:rounded-xl lg:border lg:pb-0 lg:shadow-[0_20px_60px_rgba(0,0,0,0.45)]',
          size,
          className,
        )}
      >
        <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-line lg:hidden" aria-hidden="true" />
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 id={headingId} className="truncate text-base font-semibold">
            {title}
          </h2>
          <IconButton label="关闭" onClick={onClose} className="h-11 w-11">
            <X size={18} aria-hidden="true" />
          </IconButton>
        </header>
        <div className={cn('min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4', bodyClassName)}>
          {children}
        </div>
        {footer ? <footer className="shrink-0 border-t border-line bg-panel px-4 py-3">{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  )
}

/**
 * 旧 API 兼容层：59 处调用点无需改写。
 *
 * - 宽度类（max-w-xl / max-w-5xl…）翻译为桌面端最大宽度，移动端始终满宽
 * - 旧的 my-* / lg:my-* / max-h-* / overflow-y-auto 由 Sheet 统一接管，这里丢弃
 * - 其余类（space-y-3 / grid gap-3 …）交给内容区，保持原有排版
 */
export function Dialog({
  title,
  children,
  footer,
  onClose,
  className,
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  className?: string
}) {
  const tokens = (className ?? '').split(/\s+/).filter(Boolean)
  const body: string[] = []
  let size = 'lg:max-w-lg'
  for (const token of tokens) {
    if (/^max-w-(sm|md|lg|xl|[2-7]xl)$/.test(token)) {
      size = `lg:${token}`
      continue
    }
    if (/^(lg:)?my-/.test(token) || /^max-h-/.test(token) || token === 'overflow-y-auto') continue
    body.push(token)
  }
  return (
    <Sheet title={title} onClose={onClose} size={size} bodyClassName={body.join(' ')} footer={footer}>
      {children}
    </Sheet>
  )
}


export function IconButton({
  className,
  label,
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & { label: string; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-mist transition hover:bg-white/5 hover:text-paper disabled:opacity-40 md:h-9 md:w-9',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export type ActionSheetAction = {
  label: string
  icon?: ReactNode
  tone?: 'default' | 'danger'
  disabled?: boolean
  onSelect: () => void
}

/** 移动端行操作面板：底部弹出，动作按顺序排列，危险操作使用 danger 语气。 */
export function ActionSheet({
  open,
  title,
  actions,
  onClose,
}: {
  open: boolean
  title: string
  actions: ActionSheetAction[]
  onClose: () => void
}) {
  if (!open) return null
  return (
    <Sheet title={title} onClose={onClose} size="lg:max-w-md">
      <div className="grid gap-2">
        {actions.map((action) => (
          <Button
            key={action.label}
            type="button"
            variant={action.tone === 'danger' ? 'danger' : 'line'}
            className="justify-start gap-2"
            disabled={action.disabled}
            onClick={() => {
              onClose()
              action.onSelect()
            }}
          >
            {action.icon}
            {action.label}
          </Button>
        ))}
      </div>
    </Sheet>
  )
}
