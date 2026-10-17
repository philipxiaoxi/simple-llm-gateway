import type { ReactNode } from 'react'
import { Table2 } from 'lucide-react'
import { Card } from '../ui'
import { cn } from '../../lib/utils'

export type ChartTable = {
  head: string[]
  rows: (string | number)[][]
}

/**
 * 图表卡片：标题 / 说明 / 右上角操作 + 图表主体 + 可选数据表。
 *
 * 数据表既是无障碍兜底（不依赖颜色和 hover 也能拿到数值），
 * 也是移动端想精确看数字时的入口，默认折叠不占空间。
 */
export function ChartCard({
  title,
  description,
  actions,
  children,
  table,
  tableLabel = '查看数据表',
  className,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  table?: ChartTable
  tableLabel?: string
  className?: string
}) {
  return (
    <Card className={cn('flex min-w-0 flex-col gap-3', className)}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-paper">{title}</h3>
          {description ? <p className="mt-0.5 text-xs text-mist-soft">{description}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
      {table ? (
        <details className="group/table">
          <summary className="inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded text-xs text-mist-soft transition-colors hover:text-paper focus-visible:text-paper">
            <Table2 size={12} aria-hidden="true" />
            {tableLabel}
          </summary>
          <div className="mt-2 max-h-56 overflow-auto rounded-md border border-line">
            <table className="w-full border-collapse text-left text-xs tabular-nums">
              <caption className="sr-only">{title}数据表</caption>
              <thead className="sticky top-0 bg-panel-2 text-mist">
                <tr>
                  {table.head.map((cell) => (
                    <th key={cell} scope="col" className="whitespace-nowrap px-2 py-1.5 font-medium">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="text-paper">
                {table.rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="border-t border-line/60">
                    {row.map((cell, cellIndex) =>
                      cellIndex === 0 ? (
                        <th key={cellIndex} scope="row" className="whitespace-nowrap px-2 py-1.5 font-normal">
                          {cell}
                        </th>
                      ) : (
                        <td key={cellIndex} className="whitespace-nowrap px-2 py-1.5 font-mono text-mist-strong">
                          {cell}
                        </td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </Card>
  )
}

/** 图表区空态：区间内没有数据时不留一个空坐标系 */
export function ChartEmpty({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex min-h-[180px] flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line px-4 py-10 text-center',
        className,
      )}
    >
      <div className="text-sm text-paper">{title}</div>
      {description ? <div className="max-w-md text-xs text-mist-soft">{description}</div> : null}
      {action ? <div className="mt-1.5">{action}</div> : null}
    </div>
  )
}

/** 图表加载骨架：动态导入 echarts 时占位，避免布局跳动 */
export function ChartSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('space-y-3', className)}>
      <div className="h-4 w-24 animate-pulse rounded bg-white/5" />
      <div className="h-3 w-40 animate-pulse rounded bg-white/5" />
      <div className="h-[200px] animate-pulse rounded-lg bg-white/[0.03] lg:h-[240px]" />
    </Card>
  )
}

/** 图表卡右上角的指标切换（调用次数 / Token 消耗） */
export function ChartTabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line bg-panel-2 p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            'min-h-8 rounded px-2 text-xs transition-colors',
            value === option.value ? 'bg-signal/15 text-signal' : 'text-mist-soft hover:text-paper',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
