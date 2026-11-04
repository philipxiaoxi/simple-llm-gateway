import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * shadcn/ui Card。
 *
 * 保留 `card-pad` 密度变量（舒适 / 紧凑），并补上 shadcn 的子组件结构，
 * 复杂卡片可以拆成 CardHeader / CardTitle / CardContent / CardFooter 使用。
 */
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'card-pad rounded-xl border border-border bg-card/90 text-card-foreground shadow-card transition-[border-color,box-shadow] duration-200',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('flex flex-col gap-1.5', className)}>{children}</div>
}

export function CardTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn('text-sm font-medium text-paper', className)}>{children}</h3>
}

export function CardDescription({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('text-xs text-mist-soft', className)}>{children}</p>
}

export function CardContent({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('min-w-0', className)}>{children}</div>
}

export function CardFooter({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('flex items-center gap-2', className)}>{children}</div>
}
