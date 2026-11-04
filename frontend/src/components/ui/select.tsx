import * as SelectPrimitive from '@radix-ui/react-select'
import { Check, ChevronDown, ChevronUp } from 'lucide-react'
import { Children, Fragment, isValidElement, useState } from 'react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * shadcn/ui Select（Radix），保留 `<option>` / `<optgroup>` 的调用写法。
 *
 * 全站有 88 处调用，其中大量用 `value=""` 表示「全部」。Radix 不允许空字符串作为
 * item value，所以内部用一个哨兵值占位，对外仍然把 `''` 原样透传给 `onChange`。
 * 子元素只用于解析选项，不会被渲染成真实 DOM。
 */

const EMPTY_VALUE = '__pivot_select_empty__'

type ParsedItem = { value: string; label: ReactNode; disabled?: boolean }
type ParsedGroup = { label?: ReactNode; items: ParsedItem[] }

function parseChildren(children: ReactNode): ParsedGroup[] {
  const groups: ParsedGroup[] = []
  // Radix 以 value 作为选项标识，重复值会让选中态错乱（例如同一模型挂在多个上游账号下）。
  // 这里按出现顺序去重，视觉上重复的选项本来也没有区分意义。
  const seen = new Set<string>()
  let current: ParsedGroup = { items: [] }

  const flush = () => {
    if (current.items.length || current.label) groups.push(current)
  }

  const walk = (node: ReactNode) => {
    if (node === null || node === undefined || typeof node === 'boolean') return
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    if (!isValidElement(node)) return
    if (node.type === Fragment) {
      walk((node.props as { children?: ReactNode }).children)
      return
    }
    if (node.type === 'optgroup') {
      flush()
      current = { label: (node.props as { label?: ReactNode }).label, items: [] }
      walk((node.props as { children?: ReactNode }).children)
      flush()
      current = { items: [] }
      return
    }
    if (node.type === 'option') {
      const props = node.props as { value?: string | number; children?: ReactNode; disabled?: boolean }
      const value = props.value === undefined || props.value === null ? '' : String(props.value)
      if (seen.has(toRadix(value))) return
      seen.add(toRadix(value))
      current.items.push({ value, label: props.children, disabled: props.disabled })
    }
  }

  Children.forEach(children, walk)
  flush()
  return groups
}

const toRadix = (value: string | number | undefined) =>
  value === undefined || value === null || value === '' ? EMPTY_VALUE : String(value)

const fromRadix = (value: string) => (value === EMPTY_VALUE ? '' : value)

export type SelectProps = {
  children?: ReactNode
  className?: string
  value?: string | number
  defaultValue?: string | number
  onChange?: (event: { target: { value: string } }) => void
  disabled?: boolean
  id?: string
  title?: string
  name?: string
  'aria-label'?: string
}

export function Select({
  children,
  className,
  value,
  defaultValue,
  onChange,
  disabled,
  id,
  title,
  name,
  'aria-label': ariaLabel,
}: SelectProps) {
  const groups = parseChildren(children)
  // 受控时直接用外部值；非受控时自己记一份，用于触发器展示选中项文案。
  // 不依赖 Radix 内部把 item 文本回填到 Trigger 的时机，收起态显示更可控。
  const [inner, setInner] = useState<string | number | undefined>(defaultValue)
  const current = value !== undefined ? value : inner
  const selectedLabel = findLabel(groups, current)

  return (
    <SelectPrimitive.Root
      value={value === undefined ? undefined : toRadix(value)}
      defaultValue={defaultValue === undefined ? undefined : toRadix(defaultValue)}
      onValueChange={(next) => {
        const real = fromRadix(next)
        if (value === undefined) setInner(real)
        onChange?.({ target: { value: real } })
      }}
      disabled={disabled}
      name={name}
    >
      <SelectPrimitive.Trigger
        id={id}
        title={title}
        aria-label={ariaLabel}
        className={cn(
          'group flex min-h-11 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-control py-2 pl-3 pr-3 text-left text-base text-paper outline-none transition-[border-color,box-shadow]',
          'focus:border-signal focus:ring-2 focus:ring-signal/20 disabled:cursor-not-allowed disabled:opacity-50',
          'data-[placeholder]:text-mist-soft md:min-h-9 md:text-sm',
          className,
        )}
      >
        <span className={cn('min-w-0 flex-1 truncate', selectedLabel == null && 'text-mist-soft')}>
          {selectedLabel}
        </span>
        <SelectPrimitive.Icon asChild>
          <ChevronDown
            size={14}
            aria-hidden="true"
            className="shrink-0 text-mist transition-transform duration-200 group-data-[state=open]:rotate-180"
          />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>

      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className={cn(
            'relative z-[70] max-h-[min(24rem,var(--radix-select-content-available-height))] min-w-[8rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-line-strong bg-panel-2 text-paper shadow-menu',
            'w-[var(--radix-select-trigger-width)]',
            'motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95 motion-safe:data-[state=open]:duration-150',
            'motion-safe:data-[state=closed]:animate-out motion-safe:data-[state=closed]:fade-out-0 motion-safe:data-[state=closed]:zoom-out-95 motion-safe:data-[state=closed]:duration-100',
            'motion-safe:data-[side=bottom]:slide-in-from-top-1 motion-safe:data-[side=top]:slide-in-from-bottom-1',
          )}
        >
          <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center text-mist">
            <ChevronUp size={14} aria-hidden="true" />
          </SelectPrimitive.ScrollUpButton>
          <SelectPrimitive.Viewport className="p-1">{renderGroups(groups)}</SelectPrimitive.Viewport>
          <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center text-mist">
            <ChevronDown size={14} aria-hidden="true" />
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}

function findLabel(groups: ParsedGroup[], value: string | number | undefined): ReactNode {
  if (value === undefined || value === null) return null
  const target = String(value)
  for (const group of groups) {
    for (const item of group.items) {
      if (item.value === target) return item.label
    }
  }
  return null
}

function renderGroups(groups: ParsedGroup[]) {
  const items: ReactNode[] = []
  groups.forEach((group, groupIndex) => {
    if (group.label !== undefined) {
      items.push(
        <SelectPrimitive.Group key={`group-${groupIndex}`}>
          <SelectPrimitive.Label className="px-2 py-1.5 text-[11px] uppercase tracking-[0.12em] text-mist-soft">
            {group.label}
          </SelectPrimitive.Label>
          {group.items.map((item, itemIndex) => renderItem(item, `g${groupIndex}-${itemIndex}`))}
        </SelectPrimitive.Group>,
      )
      return
    }
    group.items.forEach((item, itemIndex) => items.push(renderItem(item, `i${groupIndex}-${itemIndex}`)))
  })
  return items
}

function renderItem(item: ParsedItem, key: string) {
  return (
    <SelectPrimitive.Item
      key={key}
      value={toRadix(item.value)}
      disabled={item.disabled}
      className={cn(
        'relative flex min-h-9 cursor-pointer select-none items-center rounded-sm py-1.5 pl-2 pr-8 text-sm outline-none',
        'data-[highlighted]:bg-elevate data-[highlighted]:text-paper',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-40',
      )}
    >
      <SelectPrimitive.ItemText>
        <span className="min-w-0 truncate">{item.label}</span>
      </SelectPrimitive.ItemText>
      <span className="absolute right-2 flex h-3.5 w-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check size={14} aria-hidden="true" className="text-signal" />
        </SelectPrimitive.ItemIndicator>
      </span>
    </SelectPrimitive.Item>
  )
}
