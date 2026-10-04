import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { cn } from '../lib/utils'

/**
 * 瀑布流容器：JS 最短列装箱。
 *
 * 刻意不用 CSS `columns`：多列布局按列填充，会让最新的一条掉到第一列底部，阅读顺序错乱。
 * 这里按卡片预估高度分配到当前最矮的一列（高度相同时优先靠左，保证左→右的阅读顺序），
 * 列数随断点变化（移动 2 / 平板 3 / PC 4）。
 */
export type InfoMasonryProps<T> = {
  items: T[]
  itemKey: (item: T) => string
  /** 估算卡片总高度（px，不含列间距）；入参为当前列宽 */
  estimateHeight: (item: T, columnWidth: number) => number
  renderItem: (item: T) => ReactNode
  /** 固定列数；不传则按断点 2 / 3 / 4 */
  columns?: number
  gap?: number
  className?: string
}

const TABLET_QUERY = '(min-width: 768px)'
const DESKTOP_QUERY = '(min-width: 1024px)'

function columnsForViewport() {
  if (typeof window === 'undefined') return 2
  if (window.matchMedia(DESKTOP_QUERY).matches) return 4
  if (window.matchMedia(TABLET_QUERY).matches) return 3
  return 2
}

function useResponsiveColumns() {
  const [columns, setColumns] = useState(columnsForViewport)

  useEffect(() => {
    const queries = [DESKTOP_QUERY, TABLET_QUERY].map((query) => window.matchMedia(query))
    const update = () => setColumns(columnsForViewport())
    queries.forEach((query) => query.addEventListener('change', update))
    update()
    return () => queries.forEach((query) => query.removeEventListener('change', update))
  }, [])

  return columns
}

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const update = () => setWidth(node.clientWidth)
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return [ref, width] as const
}

function packColumns<T>(
  items: T[],
  count: number,
  estimateHeight: (item: T, columnWidth: number) => number,
  columnWidth: number,
  gap: number,
) {
  const buckets: T[][] = Array.from({ length: count }, () => [])
  // 首帧量不到容器宽度时先轮流放，避免全部堆在第一列
  if (columnWidth <= 0) {
    items.forEach((item, index) => buckets[index % count].push(item))
    return buckets
  }

  const heights = new Array<number>(count).fill(0)
  items.forEach((item) => {
    let target = 0
    for (let index = 1; index < count; index += 1) {
      if (heights[index] < heights[target]) target = index
    }
    buckets[target].push(item)
    heights[target] += Math.max(0, estimateHeight(item, columnWidth)) + gap
  })
  return buckets
}

export function InfoMasonry<T>({
  items,
  itemKey,
  estimateHeight,
  renderItem,
  columns,
  gap = 12,
  className,
}: InfoMasonryProps<T>) {
  const responsive = useResponsiveColumns()
  const [ref, width] = useElementWidth<HTMLDivElement>()
  const count = Math.max(1, Math.round(columns ?? responsive))
  const columnWidth = width > 0 ? (width - gap * (count - 1)) / count : 0
  const buckets = packColumns(items, count, estimateHeight, columnWidth, gap)

  return (
    <div ref={ref} className={cn('flex w-full items-start', className)} style={{ gap: `${gap}px` }}>
      {buckets.map((bucket, index) => (
        <div key={index} className="flex min-w-0 flex-1 flex-col" style={{ gap: `${gap}px` }}>
          {bucket.map((item) => (
            <Fragment key={itemKey(item)}>{renderItem(item)}</Fragment>
          ))}
        </div>
      ))}
    </div>
  )
}

export default InfoMasonry
