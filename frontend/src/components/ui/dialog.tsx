import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect } from 'react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { Button } from './button'

const DESKTOP_QUERY = '(min-width: 1024px)'

// 弹层打开期间同时锁住桌面端主内容滚动区（body 由 Radix 自己处理）。
// 用计数兼容嵌套弹层，避免内层关闭时提前解锁外层。
let openSheetCount = 0

/**
 * 双端弹层（shadcn/ui Dialog + Radix）：移动端底部抽屉 / 桌面居中弹窗。
 *
 * Radix 负责焦点陷阱、Esc 关闭、关闭后焦点归还与 aria 语义；
 * 这里只保留项目的移动端抽屉形态、滚动锁与尺寸约定。
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
  useEffect(() => {
    if (openSheetCount === 0) document.documentElement.dataset.modalOpen = 'true'
    openSheetCount += 1
    return () => {
      openSheetCount = Math.max(0, openSheetCount - 1)
      if (openSheetCount === 0) delete document.documentElement.dataset.modalOpen
    }
  }, [])

  return (
    <DialogPrimitive.Root open onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 z-50 bg-black/60',
            // 用 motion-safe: 而不是 motion-reduce:animate-none：data-state 变体的选择器权重
            // 高于单类，后者会被压过去，动画仍然播放。
            'motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:duration-200',
            'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=closed]:duration-150',
          )}
        />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          onPointerDownOutside={(event) => {
            // 桌面点遮罩关闭；移动端只允许按钮关闭，避免误触丢掉已填内容
            if (!window.matchMedia(DESKTOP_QUERY).matches) event.preventDefault()
          }}
          className={cn(
            'fixed inset-x-0 bottom-0 z-50 flex max-h-[90dvh] w-full min-w-0 flex-col overflow-hidden border-line bg-panel outline-none',
            'motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:slide-in-from-bottom-4 motion-safe:data-[state=open]:duration-200',
            'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=closed]:slide-out-to-bottom-4 motion-safe:data-[state=closed]:duration-150',
            // 移动：底部抽屉（长表单可直接占满视口）
            // 占满视口时为灵动岛/刘海留出顶部安全区，否则标题会被状态栏遮住
            mobileFull
              ? 'h-[100dvh] max-h-[100dvh] rounded-none border-0 pt-[var(--safe-top)] lg:pt-0'
              : 'rounded-t-2xl border-t',
            'pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-sheet',
            // 桌面：居中弹窗
            'lg:inset-x-auto lg:bottom-auto lg:left-1/2 lg:top-1/2 lg:my-0 lg:max-h-[min(84dvh,56rem)] lg:-translate-x-1/2 lg:-translate-y-1/2',
            'lg:rounded-xl lg:border lg:pb-0 lg:shadow-floating',
            'lg:motion-safe:data-[state=open]:zoom-in-95 lg:motion-safe:data-[state=closed]:zoom-out-95 lg:motion-safe:data-[state=open]:slide-in-from-bottom-0',
            size,
            className,
          )}
        >
          <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-line lg:hidden" aria-hidden="true" />
          <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
            <DialogPrimitive.Title className="truncate text-base font-semibold">{title}</DialogPrimitive.Title>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="关闭"
              title="关闭"
              className="text-mist hover:text-paper"
              onClick={onClose}
            >
              <X size={18} aria-hidden="true" />
            </Button>
          </header>
          <div className={cn('min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-4 py-4', bodyClassName)}>
            {children}
          </div>
          {footer ? <footer className="shrink-0 border-t border-line bg-panel px-4 py-3">{footer}</footer> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

/**
 * 旧 API 兼容层：36 个文件的 64 处调用点无需改写。
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
