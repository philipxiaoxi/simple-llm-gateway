import { useState } from 'react'
import { ChevronDown, SlidersHorizontal } from 'lucide-react'
import type { ReactNode } from 'react'
import { useMediaQuery } from '../lib/useMediaQuery'
import { cn } from '../lib/utils'
import { Button, Card, Sheet } from './ui'

/**
 * 筛选区：桌面端内联为卡片，移动端折叠成「筛选 · N」按钮 + 底部面板。
 *
 * 子节点始终是受控输入，因此两种形态共用同一份状态；移动端只在打开时才渲染，
 * 避免长筛选表单把首屏撑长。
 */
export function FilterBar({
  children,
  activeCount = 0,
  onReset,
  desktopClassName,
  title = '筛选',
  className,
}: {
  children: ReactNode
  activeCount?: number
  onReset?: () => void
  /** 桌面端卡片内的布局类，例如 grid gap-3 lg:grid-cols-4 */
  desktopClassName?: string
  title?: string
  className?: string
}) {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [open, setOpen] = useState(false)

  if (isDesktop) {
    return <Card className={cn(desktopClassName, className)}>{children}</Card>
  }

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <Button variant="line" className="flex-1 justify-between" onClick={() => setOpen(true)} aria-expanded={open}>
        <span className="inline-flex items-center gap-2">
          <SlidersHorizontal size={16} aria-hidden="true" />
          {title}
          {activeCount ? ` · ${activeCount}` : ''}
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </Button>
      {activeCount && onReset ? (
        <Button variant="ghost" onClick={onReset}>
          重置
        </Button>
      ) : null}
      {open ? (
        <Sheet
          title={title}
          onClose={() => setOpen(false)}
          footer={
            <div className="flex gap-2">
              <Button
                variant="ghost"
                className="flex-1"
                onClick={() => {
                  onReset?.()
                  setOpen(false)
                }}
              >
                重置
              </Button>
              <Button className="flex-1" onClick={() => setOpen(false)}>
                完成
              </Button>
            </div>
          }
        >
          <div className="grid gap-3">{children}</div>
        </Sheet>
      ) : null}
    </div>
  )
}
