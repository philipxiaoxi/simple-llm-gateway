import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'

import { cn } from '@/lib/utils'

/**
 * shadcn/ui Button，保留项目原有的 variant 名称（primary / line / ghost / danger），
 * 同时兼容 shadcn 习惯用的 default / secondary / outline / destructive。
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-[color,background-color,border-color,box-shadow,transform] duration-200 disabled:pointer-events-none disabled:opacity-40 active:scale-[0.98] [&_svg]:pointer-events-none [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-signal text-on-signal hover:brightness-110',
        default: 'bg-signal text-on-signal hover:brightness-110',
        ghost: 'bg-transparent text-paper hover:bg-elevate',
        danger: 'bg-danger/15 text-danger hover:bg-danger/25',
        destructive: 'bg-danger/15 text-danger hover:bg-danger/25',
        line: 'border border-line bg-panel-2 text-paper hover:border-mist/40 hover:bg-elevate',
        outline: 'border border-line bg-panel-2 text-paper hover:border-mist/40 hover:bg-elevate',
        secondary: 'bg-elevate text-paper hover:bg-elevate-strong',
        link: 'text-signal underline-offset-4 hover:underline',
      },
      size: {
        default: 'min-h-11 px-3 py-2 md:min-h-9',
        sm: 'min-h-9 px-2.5 py-1.5 text-xs md:min-h-8',
        lg: 'min-h-12 px-4 py-2.5',
        icon: 'h-11 w-11 md:h-9 md:w-9',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'default',
    },
  },
)

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
