import type { ReactNode } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cn } from '../lib/utils'

/**
 * 页面内容宽度上限。
 *
 * 管理端在 1920/2560 屏上如果不限制宽度，卡片和表格会被拉满，行长失控。
 * 三档宽度按页面类型选择：
 * - wide：列表 / 表格 / 看板（默认）
 * - form：表单 / 详情 / 设置
 * - prose：长文说明
 */
const WIDTHS = {
  wide: 'max-w-[1600px]',
  form: 'max-w-[1180px]',
  prose: 'max-w-[860px]',
} as const

export type PageWidth = keyof typeof WIDTHS

export function PageContainer({
  width = 'wide',
  className,
  children,
}: {
  width?: PageWidth
  className?: string
  children: ReactNode
}) {
  return <div className={cn('mx-auto w-full', WIDTHS[width], className)}>{children}</div>
}

/**
 * 页面标题区：统一 h1 尺寸、说明文案与主操作位置。
 *
 * 移动端标题在左、操作在右（可换行），桌面端同一行；详情页可传 backTo 渲染返回链接。
 */
export function PageHeader({
  title,
  description,
  actions,
  backTo,
  backLabel = '返回',
  eyebrow,
  titleBadge,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  backTo?: string
  backLabel?: string
  /** 标题上方的全大写拉丁小标签 */
  eyebrow?: ReactNode
  titleBadge?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        {backTo ? (
          <Link to={backTo} className="mb-1 inline-flex items-center gap-1 text-xs text-mist hover:text-paper">
            <ArrowLeft size={13} aria-hidden="true" /> {backLabel}
          </Link>
        ) : null}
        {eyebrow ? (
          <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">{eyebrow}</div>
        ) : null}
        <h1 className="flex min-w-0 flex-wrap items-center gap-2 text-xl font-semibold lg:text-2xl">
          <span className="min-w-0 break-words">{title}</span>
          {titleBadge}
        </h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-mist">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}
