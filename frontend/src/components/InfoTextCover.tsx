import { cn } from '../lib/utils'
import {
  coverExcerpt,
  coverTags,
  infoCoverAspect,
  infoCoverBackground,
  infoCoverPalette,
  withAlpha,
} from '../lib/info'

/**
 * 纯文字封面：所有条目统一「引言」版式，只由 cover_seed 决定配色与比例，
 * 因此同一篇内容在任何设备、任何时间刷新都是同一张封面。
 *
 * 正文里真的有 #话题 时，在页脚上方追加一行标签；没有就完全不占位。
 * 整块封面是装饰层（aria-hidden），调用方必须保证真实文本仍在 DOM 中可读
 * （卡片上用 sr-only、详情页用正文），这样屏幕阅读器与页内搜索都能命中。
 */
export function InfoTextCover({
  seed,
  text,
  channel,
  time,
  className,
}: {
  seed: number
  text: string
  channel: string
  time: string
  className?: string
}) {
  const palette = infoCoverPalette(seed)
  const aspect = infoCoverAspect(seed)
  const excerpt = coverExcerpt(text)
  const tags = coverTags(text)
  // 摘要不足 12 字：降字号、加留白，避免「孤字卡片」
  const compact = excerpt.length < 12

  return (
    <div
      aria-hidden="true"
      className={cn('relative isolate flex w-full flex-col overflow-hidden', className)}
      style={{
        aspectRatio: `${aspect.width} / ${aspect.height}`,
        backgroundImage: infoCoverBackground(seed),
      }}
    >
      <div
        className={cn(
          'flex min-h-0 flex-1 flex-col items-center justify-center text-center',
          compact ? 'px-5 pt-6' : 'px-4 pt-5',
        )}
      >
        <span
          className="mb-0.5 select-none font-serif text-3xl leading-none"
          style={{ color: withAlpha(palette.accent, 0.45) }}
        >
          “
        </span>
        {excerpt ? (
          <p
            className={cn(
              'line-clamp-4 text-balance font-medium leading-snug text-paper/95',
              compact ? 'text-[13px]' : 'text-[15px]',
            )}
          >
            {excerpt}
          </p>
        ) : null}
      </div>

      {tags.length ? (
        <div className="flex flex-wrap items-center justify-center gap-1.5 px-3 pb-1">
          {tags.map((tag) => (
            <span
              key={tag}
              className="max-w-[calc(100%-0.5rem)] truncate rounded-full border px-2 py-0.5 text-[10px] leading-4"
              style={{
                borderColor: withAlpha(palette.accent, 0.35),
                backgroundColor: withAlpha(palette.accent, 0.08),
                color: palette.accent,
              }}
            >
              #{tag}
            </span>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-center gap-1.5 px-3 pb-2.5 pt-1 text-[11px] text-paper/55">
        <span className="min-w-0 truncate">{channel}</span>
        {time ? <span className="shrink-0">· {time}</span> : null}
      </div>
    </div>
  )
}

export default InfoTextCover
