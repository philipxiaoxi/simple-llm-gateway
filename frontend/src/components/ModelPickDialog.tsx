import { useMemo, useState, type ReactNode } from 'react'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, errorMessage } from '../lib/utils'
import { Button, Dialog, Field, Input } from './ui'

export type ModelPickItem = {
  id: string
  hint?: string
}

export function ModelPickDialog({
  title,
  description,
  models,
  confirmLabel = '复制所选',
  successMessage,
  extraActions,
  buildText,
  onClose,
}: {
  title: string
  description: string
  models: ModelPickItem[]
  confirmLabel?: string
  successMessage?: (count: number) => string
  extraActions?: (selectedIds: string[]) => ReactNode
  buildText: (selectedIds: string[]) => string
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [preview, setPreview] = useState('')

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return models
    return models.filter((item) => item.id.toLowerCase().includes(keyword) || (item.hint ?? '').toLowerCase().includes(keyword))
  }, [models, query])

  const selectedSet = useMemo(() => new Set(selected), [selected])

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  function selectVisible(on: boolean) {
    const ids = filtered.map((item) => item.id)
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return [...next]
    })
  }

  function orderedSelected() {
    const chosen = new Set(selected)
    return models.filter((item) => chosen.has(item.id)).map((item) => item.id)
  }

  async function copySelected() {
    if (!selected.length) {
      notifyBad('请至少选择一个模型')
      return
    }
    const text = buildText(orderedSelected())
    try {
      await copyText(text)
      setPreview('')
      notifyOk(successMessage ? successMessage(selected.length) : `已复制 ${selected.length} 个模型`)
    } catch (error) {
      setPreview(text)
      notifyBad(errorMessage(error, '复制失败，请在下方全选复制。'))
    }
  }

  return (
    <Dialog title={title} className="my-6 max-w-2xl lg:my-[8vh]" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-mist">{description}</p>
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <Field label="搜索模型">
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入模型名过滤" />
          </Field>
          <div className="grid grid-cols-2 gap-2 sm:flex">
            <Button type="button" variant="line" className="w-full sm:w-auto" onClick={() => selectVisible(true)}>
              全选当前
            </Button>
            <Button type="button" variant="line" className="w-full sm:w-auto" onClick={() => selectVisible(false)}>
              清空当前
            </Button>
          </div>
        </div>
        <div className="max-h-[40vh] overflow-y-auto rounded-md border border-line bg-ink/40 p-2">
          {filtered.map((model) => (
            <label key={model.id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-2 hover:bg-white/5">
              <input type="checkbox" className="mt-1" checked={selectedSet.has(model.id)} onChange={() => toggle(model.id)} />
              <span className="min-w-0">
                <span className="block truncate font-mono text-xs text-paper" title={model.id}>
                  {model.id}
                </span>
                {model.hint ? <span className="mt-0.5 block text-[11px] leading-4 text-mist">{model.hint}</span> : null}
              </span>
            </label>
          ))}
          {!filtered.length ? <div className="px-2 py-6 text-center text-sm text-mist">没有匹配的模型</div> : null}
        </div>
        <div className="text-xs text-mist">
          已选 {selected.length} / {models.length}
        </div>
        {preview ? (
          <Field label="手动复制">
            <textarea
              className="h-36 w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs text-paper outline-none"
              value={preview}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
          </Field>
        ) : null}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" className="mr-auto" onClick={onClose}>
            取消
          </Button>
          {extraActions ? extraActions(orderedSelected()) : null}
          <Button type="button" onClick={() => void copySelected()}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
