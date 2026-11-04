import * as SwitchPrimitive from '@radix-ui/react-switch'
import { useId } from 'react'

import { cn } from '@/lib/utils'

/**
 * shadcn/ui Switch，外面保留原来的文字状态（开启 / 关闭）。
 *
 * 文字通过 label 关联到 Radix 的 switch 按钮，点击整块都能切换；
 * 滑块用 translate 过渡，切换时有滑行动画。
 */
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
  const id = useId()
  return (
    <label
      htmlFor={id}
      className={cn(
        'inline-flex min-h-9 shrink-0 cursor-pointer items-center gap-2 rounded-full border px-2 py-1 text-[11px] font-medium transition-colors md:min-h-7',
        checked
          ? 'border-signal/35 bg-signal/10 text-signal hover:bg-signal/15'
          : 'border-line bg-elevate text-mist hover:border-mist/40 hover:text-paper',
        disabled && 'cursor-not-allowed opacity-40',
      )}
    >
      <SwitchPrimitive.Root
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className={cn(
          'relative inline-flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 transition-colors duration-200 outline-none',
          checked ? 'bg-signal' : 'bg-track',
        )}
      >
        <SwitchPrimitive.Thumb className="block h-3 w-3 rounded-full bg-white shadow-sm transition-transform duration-200 data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0" />
      </SwitchPrimitive.Root>
      {checked ? onLabel : offLabel}
    </label>
  )
}
