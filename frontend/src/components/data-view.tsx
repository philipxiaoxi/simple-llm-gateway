import type { ReactNode } from 'react'
import { cn } from '../lib/utils'
import { Card } from './ui'

export type Column<T> = {
  key: string
  header: ReactNode
  cell: (item: T) => ReactNode
  /** 单元格附加类名（宽度、对齐等） */
  className?: string
  headerClassName?: string
}

/** 列表加载骨架：移动端卡片 / 桌面表格共用，避免「加载中…」造成布局跳动。 */
export function ListSkeleton({ rows = 6, card = true }: { rows?: number; card?: boolean }) {
  if (card) {
    return (
      <div className="grid gap-3 md:grid-cols-2 lg:hidden" aria-hidden="true">
        {Array.from({ length: rows }, (_, index) => (
          <Card key={index} className="space-y-3">
            <div className="h-4 w-1/3 animate-pulse rounded bg-white/[0.06]" />
            <div className="h-3 w-4/5 animate-pulse rounded bg-white/[0.05]" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-white/[0.05]" />
          </Card>
        ))}
      </div>
    )
  }
  return (
    <div className="hidden overflow-hidden rounded-xl border border-line lg:block" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex gap-4 border-b border-line/60 px-3 py-3 last:border-0">
          <div className="h-4 w-1/6 animate-pulse rounded bg-white/[0.06]" />
          <div className="h-4 w-1/4 animate-pulse rounded bg-white/[0.05]" />
          <div className="h-4 w-1/5 animate-pulse rounded bg-white/[0.05]" />
          <div className="h-4 w-1/6 animate-pulse rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
  className?: string
}) {
  return (
    <Card className={cn('flex flex-col items-center gap-2 p-8 text-center', className)}>
      {icon ? <div className="text-mist" aria-hidden="true">{icon}</div> : null}
      <div className="text-sm font-medium text-paper">{title}</div>
      {description ? <div className="text-sm text-mist">{description}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </Card>
  )
}

/**
 * 双端列表：桌面（≥lg）表格，移动/平板卡片。
 *
 * 之前的每个列表页各自手写「hidden lg:block 表格 + lg:hidden 卡片」两套视图，
 * 加载态与空态也各写一份；统一到这里后只维护一份数据定义。
 */
export function DataView<T>({
  items,
  columns,
  keyOf,
  renderCard,
  loading,
  empty,
  className,
  onRowClick,
}: {
  items: T[]
  columns: Column<T>[]
  keyOf: (item: T) => string
  renderCard: (item: T) => ReactNode
  loading?: boolean
  empty?: ReactNode
  className?: string
  onRowClick?: (item: T) => void
}) {
  if (loading) {
    return (
      <div role="status" aria-live="polite">
        <span className="sr-only">加载中</span>
        <ListSkeleton rows={6} card />
        <div className="hidden lg:block">
          <ListSkeleton rows={6} card={false} />
        </div>
      </div>
    )
  }
  if (!items.length) return <>{empty}</>
  return (
    <>
      <div className={cn('hidden overflow-x-auto rounded-xl border border-line lg:block', className)}>
        <table className="min-w-full text-left text-sm">
          <thead className="bg-panel text-mist">
            <tr>
              {columns.map((column) => (
                <th key={column.key} className={cn('dense-row px-3 font-medium', column.headerClassName)}>
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={keyOf(item)}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
                className={cn(
                  'border-t border-line align-top transition hover:bg-white/[0.03]',
                  onRowClick && 'cursor-pointer',
                )}
              >
                {columns.map((column) => (
                  <td key={column.key} className={cn('dense-row px-3', column.className)}>
                    {column.cell(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid min-w-0 gap-3 md:grid-cols-2 lg:hidden">
        {items.map((item) => (
          <div
            key={keyOf(item)}
            role={onRowClick ? 'button' : undefined}
            tabIndex={onRowClick ? 0 : undefined}
            onClick={onRowClick ? () => onRowClick(item) : undefined}
            onKeyDown={
              onRowClick
                ? (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onRowClick(item)
                    }
                  }
                : undefined
            }
            className={cn('min-w-0', onRowClick && 'cursor-pointer')}
          >
            {renderCard(item)}
          </div>
        ))}
      </div>
    </>
  )
}
