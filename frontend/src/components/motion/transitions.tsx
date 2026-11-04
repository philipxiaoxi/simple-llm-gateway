import { motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

const EASE = [0.22, 1, 0.36, 1] as const

/**
 * 路由切换过渡：淡入。
 *
 * 只动 opacity：页面里有 `position: fixed` 元素（悬浮按钮、全屏详情），
 * 一旦给祖先加 transform/filter 就会改变它们的定位参照，这里刻意不做位移。
 * 用法上把 `key` 设为一级路径（见 Layout），切区时重新挂载即重播。
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion()
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

/**
 * 区块入场：用于概览等由多张卡片组成的页面，按顺序轻微上浮。
 * `index` 控制错峰，避免所有卡片同时出现。
 */
export function Reveal({
  children,
  index = 0,
  className,
}: {
  children: ReactNode
  index?: number
  className?: string
}) {
  const reduce = useReducedMotion()
  return (
    <motion.div
      className={cn(className)}
      initial={reduce ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: EASE, delay: reduce ? 0 : Math.min(index, 6) * 0.04 }}
    >
      {children}
    </motion.div>
  )
}

/** 数值变化时做一次轻微缩放，用于 KPI 卡片刷新反馈 */
export function ValuePop({ children, className }: { children: ReactNode; className?: string }) {
  const reduce = useReducedMotion()
  return (
    <motion.span
      key={String(children)}
      className={cn('inline-block', className)}
      initial={reduce ? false : { opacity: 0.5, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2, ease: EASE }}
    >
      {children}
    </motion.span>
  )
}
