import { Button } from './ui'
import { cn } from '../lib/utils'

function pageWindow(current: number, total: number, siblings: number): Array<number | 'ellipsis'> {
  if (total <= 1) return [1]
  const pages = new Set<number>([1, total])
  for (let index = current - siblings; index <= current + siblings; index += 1) {
    if (index >= 1 && index <= total) pages.add(index)
  }
  const sorted = [...pages].sort((left, right) => left - right)
  const items: Array<number | 'ellipsis'> = []
  sorted.forEach((value, index) => {
    if (index > 0 && value - sorted[index - 1] > 1) items.push('ellipsis')
    items.push(value)
  })
  return items
}

function PageNumberButton({
  page,
  current,
  onPage,
}: {
  page: number
  current: number
  onPage: (page: number) => void
}) {
  const active = page === current
  return (
    <button
      type="button"
      aria-label={`第 ${page} 页`}
      aria-current={active ? 'page' : undefined}
      onClick={() => onPage(page)}
      className={cn(
        'inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-2 text-sm font-medium tabular-nums transition lg:min-h-9 lg:min-w-9',
        active ? 'bg-signal text-ink' : 'border border-line bg-panel-2 text-paper hover:border-mist/40',
      )}
    >
      {page}
    </button>
  )
}

export function Pagination({
  page,
  pageCount,
  total,
  unit = '条',
  onPage,
}: {
  page: number
  pageCount: number
  total: number
  unit?: string
  onPage: (page: number) => void
}) {
  const mobileItems = pageWindow(page, pageCount, 0)
  const desktopItems = pageWindow(page, pageCount, 1)
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="text-sm text-mist">
        共 {total} {unit} · 第 {page} / {pageCount} 页
      </div>
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
        <div className="grid grid-cols-2 gap-2 lg:flex">
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
          >
            上一页
          </Button>
          <Button
            type="button"
            variant="line"
            className="w-full lg:w-auto"
            disabled={page >= pageCount}
            onClick={() => onPage(page + 1)}
          >
            下一页
          </Button>
        </div>
        <div className="flex flex-wrap justify-center gap-1 lg:hidden">
          {mobileItems.map((item, index) =>
            item === 'ellipsis' ? (
              <span key={`m-ellipsis-${index}`} className="inline-flex min-h-11 min-w-8 items-center justify-center text-mist">
                …
              </span>
            ) : (
              <PageNumberButton key={`m-${item}`} page={item} current={page} onPage={onPage} />
            ),
          )}
        </div>
        <div className="hidden flex-wrap gap-1 lg:flex">
          {desktopItems.map((item, index) =>
            item === 'ellipsis' ? (
              <span key={`d-ellipsis-${index}`} className="inline-flex min-h-9 min-w-8 items-center justify-center text-mist">
                …
              </span>
            ) : (
              <PageNumberButton key={`d-${item}`} page={item} current={page} onPage={onPage} />
            ),
          )}
        </div>
      </div>
    </div>
  )
}
