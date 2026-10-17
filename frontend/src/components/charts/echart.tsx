import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ChartOption } from '../../lib/chart-theme'
import { cn } from '../../lib/utils'

// 按需注册：只打包柱状/折线 + 网格/图例/提示框，避免把整个 echarts 塞进主包。
// 该模块只被动态导入的图表区引用，资源页等其它路由不会付出这份体积。
echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

let prefersReducedMotion = false
if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
  prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * ECharts 薄封装：负责实例生命周期、容器尺寸自适应与无障碍标签。
 *
 * 高度交给外层的 className（如 h-[240px] lg:h-[280px]），
 * 容器尺寸变化由 ResizeObserver 触发 resize，侧栏折叠 / 横竖屏切换都能跟上。
 */
export function EChart({
  option,
  ariaLabel,
  className,
  style,
}: {
  option: ChartOption
  /** 屏幕阅读器读到的图表摘要（数据表兜底由 ChartCard 提供） */
  ariaLabel: string
  className?: string
  /** 需要按数据条数算高度时用内联样式，比拼接 Tailwind 任意值稳 */
  style?: CSSProperties
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const chart = echarts.init(host, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => {
      if (!chart.isDisposed()) chart.resize()
    })
    observer.observe(host)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption(
      {
        ...option,
        animation: !prefersReducedMotion,
        animationDuration: 420,
        animationDurationUpdate: 220,
      },
      { notMerge: true },
    )
  }, [option])

  return <div ref={hostRef} className={cn('w-full', className)} style={style} role="img" aria-label={ariaLabel} />
}
