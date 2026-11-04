import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { cn } from '../lib/utils'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card } from './ui/card'
import { Dialog, Sheet } from './ui/dialog'
import { Field } from './ui/label'
import { Input } from './ui/input'
import { Select } from './ui/select'
import { Switch } from './ui/switch'

/**
 * 兼容层：全站仍从 `components/ui` 引入基础组件，
 * 具体实现已经迁到 shadcn/ui（`components/ui/*`）。
 */
export { Badge, Button, Card, Dialog, Field, Input, Select, Sheet, Switch }

export function IconButton({
  className,
  label,
  children,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> & { label: string; children: ReactNode }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      className={cn('text-mist hover:text-paper', className)}
      {...props}
    >
      {children}
    </Button>
  )
}

export type ActionSheetAction = {
  label: string
  icon?: ReactNode
  tone?: 'default' | 'danger'
  disabled?: boolean
  onSelect: () => void
}

/** 移动端行操作面板：底部弹出，动作按顺序排列，危险操作使用 danger 语气。 */
export function ActionSheet({
  open,
  title,
  actions,
  onClose,
}: {
  open: boolean
  title: string
  actions: ActionSheetAction[]
  onClose: () => void
}) {
  if (!open) return null
  return (
    <Sheet title={title} onClose={onClose} size="lg:max-w-md">
      <div className="grid gap-2">
        {actions.map((action) => (
          <Button
            key={action.label}
            type="button"
            variant={action.tone === 'danger' ? 'danger' : 'line'}
            className="justify-start gap-2"
            disabled={action.disabled}
            onClick={() => {
              onClose()
              action.onSelect()
            }}
          >
            {action.icon}
            {action.label}
          </Button>
        ))}
      </div>
    </Sheet>
  )
}
