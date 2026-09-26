import type { ReactNode } from 'react'
import { accountColor } from '../lib/accountModels'
import { cn } from '../lib/utils'
import { ItemPickDialog, type PickItem } from './ItemPickDialog'

export type ModelPickItem = {
  id: string
  hint?: string
  accountName?: string
  accountIndex?: number
}

export function ModelPickDialog({
  title,
  description,
  models,
  confirmLabel = '复制所选',
  successMessage,
  extraActions,
  buildText,
  onClose,
}: {
  title: string
  description: string
  models: ModelPickItem[]
  confirmLabel?: string
  successMessage?: (count: number) => string
  extraActions?: (selectedIds: string[]) => ReactNode
  buildText: (selectedIds: string[]) => string
  onClose: () => void
}) {
  function renderModel(item: PickItem) {
    const color = accountColor(item.accountIndex ?? 0)
    return (
      <span className={cn('min-w-0 border-l-2 pl-2', item.accountName ? color.border : 'border-transparent')}>
        <span className="block truncate font-mono text-xs text-paper" title={item.id}>
          {item.id}
        </span>
        {item.accountName ? (
          <span className={cn('mt-0.5 block text-[11px] leading-4', color.text)}>
            优先 {(item.accountIndex ?? 0) + 1} · {item.accountName}
          </span>
        ) : null}
        {item.hint ? <span className="block text-[11px] leading-4 text-mist">{item.hint}</span> : null}
      </span>
    )
  }

  return (
    <ItemPickDialog
      title={title}
      description={description}
      items={models}
      searchPlaceholder="输入模型名过滤"
      unit="模型"
      emptyLabel="没有匹配的模型"
      confirmLabel={confirmLabel}
      successMessage={successMessage}
      extraActions={extraActions}
      renderItem={renderModel}
      buildText={buildText}
      onClose={onClose}
    />
  )
}
