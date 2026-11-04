import type { ComponentPropsWithRef } from 'react'

import { cn } from '@/lib/utils'

/** shadcn/ui Input：移动端保留 16px 字号，避免 iOS 聚焦时自动缩放 */
export function Input({ className, ...props }: ComponentPropsWithRef<'input'>) {
  return (
    <input
      className={cn(
        'flex min-h-11 w-full min-w-0 rounded-md border border-input bg-control px-3 py-2 text-base text-paper outline-none transition-[border-color,box-shadow] placeholder:text-mist-soft focus:border-signal focus:ring-2 focus:ring-signal/20 disabled:cursor-not-allowed disabled:opacity-50 md:min-h-9 md:text-sm',
        className,
      )}
      {...props}
    />
  )
}
