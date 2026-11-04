import { cva, type VariantProps } from 'class-variance-authority'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      tone: {
        ok: 'bg-signal/15 text-signal',
        bad: 'bg-danger/15 text-danger',
        warn: 'bg-warn/15 text-warn',
        mist: 'bg-elevate text-mist',
        info: 'bg-info/15 text-info',
        // shadcn 习惯命名
        secondary: 'bg-elevate text-mist',
        destructive: 'bg-danger/15 text-danger',
        outline: 'border border-border text-mist',
      },
    },
    defaultVariants: {
      tone: 'mist',
    },
  },
)

export function Badge({
  children,
  tone,
  title,
  className,
}: {
  children: ReactNode
  title?: string
  className?: string
} & VariantProps<typeof badgeVariants>) {
  return (
    <span title={title} className={cn(badgeVariants({ tone }), className)}>
      {children}
    </span>
  )
}
