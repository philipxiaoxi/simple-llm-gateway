import { Check } from 'lucide-react'
import type { InputHTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Checkbox({ children, className, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'children'> & {
  children: ReactNode
}) {
  return (
    <label className={cn('flex min-h-9 min-w-0 cursor-pointer items-center gap-2.5 text-sm leading-5 has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50', className)}>
      <span className="relative flex size-4 shrink-0 items-center justify-center">
        <input {...props} type="checkbox" className="peer absolute inset-0 m-0 size-full cursor-inherit opacity-0" />
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 rounded border border-mist/60 bg-panel peer-checked:border-signal peer-checked:bg-signal peer-focus-visible:ring-2 peer-focus-visible:ring-signal/70 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-panel" />
        <Check aria-hidden="true" size={12} strokeWidth={3} className="pointer-events-none relative text-ink opacity-0 peer-checked:opacity-100" />
      </span>
      <span className="min-w-0 break-words [overflow-wrap:anywhere]">{children}</span>
    </label>
  )
}
