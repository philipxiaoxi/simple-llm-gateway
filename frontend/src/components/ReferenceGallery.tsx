import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ExternalLink, Image as ImageIcon, Link2, Loader2, Plus, Search, Trash2 } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'

import {
  api,
  type ReferenceItem,
  type ReferenceKind,
  type ReferenceLabels,
  type ReferenceQuery,
} from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { Pagination } from './Pagination'
import { Button, Dialog, Input, Select, Switch } from './ui'

const PAGE_SIZE = 24
const SCOPES: { value: NonNullable<ReferenceQuery['scope']>; label: string }[] = [
  { value: 'all', label: '全部范围' },
  { value: 'featured', label: '精选 / 热门' },
  { value: 'curated', label: '仅精选' },
  { value: 'hot', label: '仅热门' },
]
const KINDS: { value: ReferenceKind; label: string }[] = [
  { value: 'portrait', label: '人像' },
  { value: 'pose', label: '姿势' },
  { value: 'costume', label: '服装' },
]
const DEFAULT_LABELS: ReferenceLabels = { group: '分类', category: '分类', detail: '细节', tag: '标签' }

export function ReferenceGallery({
  kind,
  onPick,
  compact = false,
}: {
  kind: ReferenceKind
  onPick?: (item: ReferenceItem) => Promise<void> | void
  compact?: boolean
}) {
  const [queryInput, setQueryInput] = useState('')
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState('')
  const [category, setCategory] = useState('')
  const [tag, setTag] = useState('')
  const [detail, setDetail] = useState('')
  const [scope, setScope] = useState<NonNullable<ReferenceQuery['scope']>>('all')
  const [includeAdult, setIncludeAdult] = useState(false)
  const [page, setPage] = useState(1)
  const [preview, setPreview] = useState<ReferenceItem | null>(null)
  const [pickingId, setPickingId] = useState('')
  const [adding, setAdding] = useState(false)
  const canCreate = useCan(`mcp_${kind}s:create`)
  const canDelete = useCan(`mcp_${kind}s:delete`)

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [queryInput])

  useEffect(() => {
    setPage(1)
  }, [query, group, category, tag, detail, scope, includeAdult])

  const meta = useQuery({
    queryKey: ['references', 'meta', kind, includeAdult],
    queryFn: () => api.referenceMeta(kind, { include_adult: includeAdult }),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const list = useQuery({
    queryKey: ['references', 'list', kind, query, group, category, tag, detail, scope, includeAdult, page],
    queryFn: () =>
      api.referenceList(kind, {
        q: query,
        group,
        category,
        tag,
        detail,
        scope,
        include_adult: includeAdult,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    placeholderData: (previous) => previous,
  })

  const data = list.data
  const items = data?.items ?? []
  const labels = data?.labels ?? meta.data?.labels ?? DEFAULT_LABELS
  const facets = data?.facets ?? meta.data?.facets
  const groups = facets?.groups ?? []
  const categories = facets?.categories ?? []
  const tags = facets?.tags ?? []
  const details = facets?.details ?? []
  const hiddenAdult = data?.hidden_adult ?? meta.data?.hidden_adult ?? 0
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))
  const isPortrait = kind === 'portrait'
  const supportsAdultToggle = kind !== 'portrait'

  async function handlePick(item: ReferenceItem) {
    if (!onPick) return
    setPickingId(item.id)
    try {
      await onPick(item)
    } finally {
      setPickingId('')
    }
  }

  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: (id: string) => api.referenceDelete(kind, id),
    onSuccess: () => {
      notifyOk('已删除本地参考')
      setPreview(null)
      void queryClient.invalidateQueries({ queryKey: ['references'] })
    },
    onError: (error) => notifyBad(errorMessage(error, '删除失败')),
  })

  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text)
      notifyOk(`${label}已复制`)
    } catch {
      notifyBad('复制失败，请手动复制')
    }
  }

  return (
    <div className="space-y-4">
      {canCreate && !compact ? (
        <div className="flex justify-end">
          <Button type="button" onClick={() => setAdding(true)}>
            <Plus size={15} /> 添加参考
          </Button>
        </div>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
          <Input
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            placeholder="搜索标题、分类、id"
            className="pl-9"
          />
        </div>
        <Select value={group} onChange={(event) => setGroup(event.target.value)}>
          <option value="">全部{labels.group}</option>
          {groups.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </Select>
        {categories.length ? (
          <Select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">全部{labels.category}</option>
            {categories.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        ) : null}
        {details.length && details.length <= 20 ? (
          <Select value={detail} onChange={(event) => setDetail(event.target.value)}>
            <option value="">全部{labels.detail}</option>
            {details.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        ) : null}
        {tags.length ? (
          <Select value={tag} onChange={(event) => setTag(event.target.value)}>
            <option value="">全部{labels.tag}</option>
            {tags.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        ) : null}
        {isPortrait ? (
          <Select
            value={scope}
            onChange={(event) => setScope(event.target.value as NonNullable<ReferenceQuery['scope']>)}
          >
            {SCOPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        ) : null}
        {supportsAdultToggle ? (
          <div className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-1.5">
            <span className="text-xs text-mist">包含 18+{hiddenAdult ? `（隐藏 ${hiddenAdult}）` : ''}</span>
            <Switch checked={includeAdult} onCheckedChange={setIncludeAdult} onLabel="开" offLabel="关" />
          </div>
        ) : null}
      </div>

      {data?.stale ? (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          上游暂不可用，当前展示缓存数据（{formatTime(data.synced_at)}）。
        </div>
      ) : null}
      {meta.isError && !data ? (
        <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          {errorMessage(meta.error, '参考图库不可用')}
        </div>
      ) : null}
      {list.isError ? <div className="text-sm text-danger">{errorMessage(list.error, '加载参考图失败')}</div> : null}

      {items.length ? (
        <div
          className={cn(
            'grid',
            compact
              ? 'grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 2xl:grid-cols-8'
              : 'grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5',
          )}
        >
          {items.map((item) => (
            <ReferenceCard
              key={item.id}
              item={item}
              compact={compact}
              picking={pickingId === item.id}
              onPreview={() => setPreview(item)}
              onPick={onPick ? () => void handlePick(item) : undefined}
              onCopy={copyText}
              onDelete={canDelete && item.source === 'local' ? () => remove.mutate(item.id) : undefined}
            />
          ))}
        </div>
      ) : !list.isPending ? (
        <div className="rounded-lg border border-dashed border-line py-10 text-center text-sm text-mist">
          没有匹配的参考图
        </div>
      ) : (
        <div className="text-sm text-mist">加载中…</div>
      )}

      <Pagination page={page} pageCount={pageCount} total={data?.total ?? 0} unit="条" onPage={setPage} />

      {data?.attribution ? <div className="text-[11px] leading-5 text-mist">{data.attribution}</div> : null}
      {adding ? (
        <ReferenceCreateDialog
          kind={kind}
          labels={labels}
          groups={groups}
          categories={categories}
          onClose={() => setAdding(false)}
        />
      ) : null}

      {preview ? (
        <Dialog title={preview.title} onClose={() => setPreview(null)} className="max-w-3xl">
          <div className="space-y-3">
            <img
              src={preview.image_url || preview.thumb_url}
              alt={preview.title}
              className="max-h-[70vh] w-full rounded-lg border border-line object-contain"
            />
            <div className="text-xs text-mist">
              {[preview.group, preview.category, preview.detail].filter(Boolean).join(' · ')} · {preview.width}×
              {preview.height}
            </div>
            {preview.tags.length ? <div className="text-xs text-mist">{preview.tags.join('、')}</div> : null}
            <div className="rounded-md border border-line bg-panel-2 px-3 py-2 text-sm text-paper">
              {preview.prompt}
            </div>
            <div className="flex flex-wrap gap-2">
              {onPick ? (
                <Button type="button" onClick={() => void handlePick(preview)} disabled={Boolean(pickingId)}>
                  {pickingId ? '添加中…' : '用作参考'}
                </Button>
              ) : null}
              <Button type="button" variant="line" onClick={() => void copyText(preview.image_url, '图片链接')}>
                <Link2 size={14} /> 复制链接
              </Button>
              <Button type="button" variant="line" onClick={() => void copyText(preview.prompt, '提示词')}>
                <Copy size={14} /> 复制提示词
              </Button>
              <a
                href={preview.image_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-2 text-sm text-mist transition hover:text-paper"
              >
                <ExternalLink size={14} /> 原图
              </a>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

export function ReferencePickerDialog({
  title = '参考图库',
  kind: initialKind = 'portrait',
  onClose,
  onPick,
}: {
  title?: string
  kind?: ReferenceKind
  onClose: () => void
  onPick: (item: ReferenceItem) => Promise<void> | void
}) {
  const [kind, setKind] = useState<ReferenceKind>(initialKind)
  return (
    <Dialog title={title} onClose={onClose} className="max-w-7xl">
      <div className="space-y-4">
        <div className="flex flex-wrap gap-1.5">
          {KINDS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setKind(option.value)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs transition',
                kind === option.value
                  ? 'border-signal/50 bg-signal/15 text-signal'
                  : 'border-line text-mist hover:text-paper',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <ReferenceGallery key={kind} kind={kind} onPick={onPick} compact />
      </div>
    </Dialog>
  )
}

function ReferenceCreateDialog({
  kind,
  labels,
  groups,
  categories,
  onClose,
}: {
  kind: ReferenceKind
  labels: ReferenceLabels
  groups: string[]
  categories: string[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')
  const [group, setGroup] = useState('')
  const [category, setCategory] = useState('')
  const [detail, setDetail] = useState('')
  const [tags, setTags] = useState('')
  const [prompt, setPrompt] = useState('')
  const [adult, setAdult] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const create = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请选择图片')
      return api.referenceCreate(kind, { file, title, group, category, detail, tags, prompt, adult })
    },
    onSuccess: () => {
      notifyOk('已添加参考')
      void queryClient.invalidateQueries({ queryKey: ['references'] })
      onClose()
    },
    onError: (error) => notifyBad(errorMessage(error, '添加失败')),
  })

  return (
    <Dialog title="添加参考" onClose={onClose} className="max-w-xl">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        <label className="space-y-1 text-xs text-mist sm:col-span-2">
          图片
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="block w-full text-sm text-paper file:mr-3 file:rounded-md file:border-0 file:bg-panel-2 file:px-3 file:py-2"
          />
        </label>
        <label className="space-y-1 text-xs text-mist sm:col-span-2">
          标题
          <input value={title} onChange={(event) => setTitle(event.target.value)} required className="w-full rounded-md border border-line bg-control px-3 py-2 text-sm text-paper outline-none focus:border-signal" />
        </label>
        <label className="space-y-1 text-xs text-mist">
          {labels.group}
          <Input value={group} onChange={(event) => setGroup(event.target.value)} list={`${kind}-groups`} />
          <datalist id={`${kind}-groups`}>
            {groups.map((value) => <option key={value} value={value} />)}
          </datalist>
        </label>
        <label className="space-y-1 text-xs text-mist">
          {labels.category}
          <Input value={category} onChange={(event) => setCategory(event.target.value)} list={`${kind}-categories`} />
          <datalist id={`${kind}-categories`}>
            {categories.map((value) => <option key={value} value={value} />)}
          </datalist>
        </label>
        <label className="space-y-1 text-xs text-mist">
          {labels.detail}
          <Input value={detail} onChange={(event) => setDetail(event.target.value)} />
        </label>
        <label className="space-y-1 text-xs text-mist">
          标签
          <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="用逗号分隔" className="w-full rounded-md border border-line bg-control px-3 py-2 text-sm text-paper outline-none focus:border-signal" />
        </label>
        <label className="space-y-1 text-xs text-mist sm:col-span-2">
          提示词
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            rows={3}
            className="w-full rounded-md border border-line bg-control px-3 py-2 text-sm text-paper outline-none focus:border-signal"
          />
        </label>
        {kind !== 'portrait' ? (
          <div className="flex items-center justify-between rounded-md border border-line px-3 py-2 sm:col-span-2">
            <span className="text-xs text-mist">标记为 18+</span>
            <Switch checked={adult} onCheckedChange={setAdult} />
          </div>
        ) : null}
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button type="button" variant="line" onClick={onClose}>取消</Button>
          <Button type="submit" disabled={create.isPending || !file || !title.trim()}>
            {create.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} 保存
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function ReferenceCard({
  item,
  compact,
  picking,
  onPreview,
  onPick,
  onCopy,
  onDelete,
}: {
  item: ReferenceItem
  compact: boolean
  picking: boolean
  onPreview: () => void
  onPick?: () => void
  onCopy: (text: string, label: string) => void
  onDelete?: () => void
}) {
  const badges = (
    <>
      {item.curated ? <OverlayTag tone="curated">精选</OverlayTag> : null}
      {item.hot ? <OverlayTag tone="hot">热门</OverlayTag> : null}
      {item.adult ? <OverlayTag tone="adult">18+</OverlayTag> : null}
      {item.source === 'local' ? <OverlayTag tone="curated">本地</OverlayTag> : null}
    </>
  )

  if (compact) {
    return (
      <div className="group overflow-hidden rounded-lg border border-line bg-panel">
        <div className="relative">
          <button type="button" onClick={onPreview} className="block w-full overflow-hidden">
            <img
              src={item.thumb_url}
              alt={item.title}
              loading="lazy"
              className="aspect-[3/4] w-full object-cover transition duration-300 group-hover:scale-[1.04]"
            />
          </button>
          <div className="absolute left-1.5 top-1.5 flex flex-wrap gap-1">{badges}</div>
          {onPick ? (
            <button
              type="button"
              onClick={onPick}
              disabled={picking}
              className="absolute inset-x-1.5 bottom-1.5 inline-flex items-center justify-center gap-1 rounded-md bg-signal px-2 py-1 text-[11px] font-medium text-on-signal opacity-100 shadow-sm transition hover:bg-signal/90 disabled:opacity-60 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-visible:opacity-100"
            >
              {picking ? <Loader2 size={12} className="animate-spin" /> : <ImageIcon size={12} />}
              用作参考
            </button>
          ) : null}
        </div>
        <div className="truncate px-2 py-1.5 text-[11px] text-paper" title={item.title}>
          {item.title}
        </div>
      </div>
    )
  }

  return (
    <div className="group overflow-hidden rounded-xl border border-line bg-panel">
      <button type="button" onClick={onPreview} className="relative block w-full overflow-hidden">
        <img
          src={item.thumb_url}
          alt={item.title}
          loading="lazy"
          className="aspect-[4/5] w-full object-cover transition duration-300 group-hover:scale-[1.03]"
        />
        <div className="absolute left-2 top-2 flex flex-wrap gap-1">{badges}</div>
      </button>
      <div className="space-y-2 p-2.5">
        <div className="truncate text-sm text-paper" title={item.title}>
          {item.title}
        </div>
        <div className="truncate text-[11px] text-mist">
          {[item.group, item.category, item.detail].filter(Boolean).join(' · ')}
        </div>
        <div className="flex flex-wrap gap-1">
          {onPick ? (
            <Button
              type="button"
              className="min-h-8 px-2 py-1 text-xs"
              onClick={onPick}
              disabled={picking}
            >
              {picking ? <Loader2 size={13} className="animate-spin" /> : <ImageIcon size={13} />}
              用作参考
            </Button>
          ) : null}
          <Button
            type="button"
            variant="line"
            className="min-h-8 px-2 py-1 text-xs"
            onClick={() => onCopy(item.prompt, '提示词')}
          >
            <Copy size={13} /> 提示词
          </Button>
          <Button
            type="button"
            variant="line"
            className="min-h-8 px-2 py-1 text-xs"
            onClick={() => onCopy(item.image_url, '图片链接')}
          >
            <Link2 size={13} /> 链接
          </Button>
          {onDelete ? (
            <Button type="button" variant="line" className="min-h-8 px-2 py-1 text-xs" onClick={onDelete}>
              <Trash2 size={13} /> 删除
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function OverlayTag({ tone, children }: { tone: 'curated' | 'hot' | 'adult'; children: ReactNode }) {
  const map = {
    curated: 'bg-signal text-on-signal',
    hot: 'bg-warn text-on-signal',
    adult: 'bg-danger text-white',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold shadow-sm ring-1 ring-black/10',
        map[tone],
      )}
    >
      {children}
    </span>
  )
}

function formatTime(value?: string): string {
  if (!value) return '未知'
  return value.slice(0, 19).replace('T', ' ')
}
