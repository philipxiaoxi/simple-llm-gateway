import { cn } from '../../lib/utils'

export type LegendItem = { name: string; color: string }

/**
 * 图表图例：横向可滚动的色块 + 名称，点击切换系列显示。
 *
 * 没有用 ECharts 内置图例，因为它按「每页均分」分页时会把最后一个图例切在文字中间；
 * 这里换成 DOM 实现：一行横向滑动（触屏/触控板/滚动条都行），可读性和触控都更稳。
 * 切换状态通过 legend.selected 回传给 ECharts，堆叠与坐标轴会自动重算。
 */
export function SeriesLegend({
  items,
  hidden,
  onToggle,
  onShowAll,
  label = '图例：点击切换显示',
}: {
  items: LegendItem[]
  hidden: ReadonlySet<string>
  onToggle: (name: string) => void
  onShowAll: () => void
  label?: string
}) {
  if (items.length === 0) return null
  return (
    <div className="mt-2 flex min-w-0 items-center gap-2">
      <div className="relative min-w-0 flex-1">
        <div
          role="group"
          aria-label={label}
          className="legend-scroll -mx-1 flex min-w-0 gap-1 overflow-x-auto px-1 pb-1"
        >
          {items.map((item) => {
            const visible = !hidden.has(item.name)
            return (
              <button
                key={item.name}
                type="button"
                aria-pressed={visible}
                title={item.name}
                onClick={() => onToggle(item.name)}
                className={cn(
                  'inline-flex min-h-8 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-xs transition-colors hover:bg-white/5',
                  visible ? 'text-mist-strong' : 'text-mist-soft/70',
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn('h-2 w-2 shrink-0 rounded-[2px] transition-colors', !visible && 'opacity-40')}
                  style={{ background: item.color }}
                />
                <span className={cn('max-w-[8.5rem] truncate', !visible && 'line-through')}>{item.name}</span>
              </button>
            )
          })}
        </div>
        {/* 右侧渐隐：提示图例还有更多，可以横向滑/滚过去 */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-panel to-transparent"
        />
      </div>
      {hidden.size > 0 ? (
        <button
          type="button"
          onClick={onShowAll}
          className="min-h-8 shrink-0 rounded-md px-1.5 text-xs text-signal transition-colors hover:bg-signal/10"
        >
          全部显示
        </button>
      ) : null}
    </div>
  )
}
