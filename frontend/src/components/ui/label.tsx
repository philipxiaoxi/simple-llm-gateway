import * as LabelPrimitive from '@radix-ui/react-label'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** shadcn/ui Label：用 Radix 保证 htmlFor / 点击聚焦行为正确 */
export function Label({
  children,
  className,
  htmlFor,
}: {
  children: ReactNode
  className?: string
  htmlFor?: string
}) {
  return (
    <LabelPrimitive.Root
      htmlFor={htmlFor}
      className={cn('text-xs uppercase tracking-[0.16em] text-mist', className)}
    >
      {children}
    </LabelPrimitive.Root>
  )
}

/** 表单字段：标签 + 控件，项目内统一入口（原 Field） */
export function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  if (htmlFor) {
    return <div className="min-w-0 space-y-1.5"><Label className="block" htmlFor={htmlFor}>{label}</Label>{children}</div>
  }
  return (
    <LabelPrimitive.Root className="block min-w-0 space-y-1.5">
      <span className="block text-xs uppercase tracking-[0.16em] text-mist">{label}</span>
      {children}
    </LabelPrimitive.Root>
  )
}
