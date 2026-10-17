import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button, Dialog, Field, Input, Select } from './ui'
import type { Account } from '../lib/api'
import { keyOf, type Target } from '../lib/benchmark'
import { cn } from '../lib/utils'

export function BenchmarkModelPicker({
  targets,
  selected,
  onChange,
  onClose,
}: {
  targets: Target[]
  selected: string[]
  onChange: (keys: string[]) => void
  onClose: () => void
}) {
  const [sourceFilter, setSourceFilter] = useState<'all' | 'upstream' | 'agent'>('all')
  const [providerFilter, setProviderFilter] = useState('all')
  const [accountFilter, setAccountFilter] = useState('all')
  const [search, setSearch] = useState('')

  const providers = useMemo(() => [...new Set(targets.map((target) => target.account.provider))], [targets])
  const accountOptions = useMemo(() => {
    const map = new Map<number, Account>()
    targets.forEach((target) => {
      if ((sourceFilter === 'all' || target.account.source === sourceFilter) && (providerFilter === 'all' || target.account.provider === providerFilter)) {
        map.set(target.account.id, target.account)
      }
    })
    return [...map.values()]
  }, [providerFilter, sourceFilter, targets])
  const filtered = useMemo(
    () =>
      targets.filter(
        (target) =>
          (sourceFilter === 'all' || target.account.source === sourceFilter) &&
          (providerFilter === 'all' || target.account.provider === providerFilter) &&
          (accountFilter === 'all' || String(target.account.id) === accountFilter),
      ),
    [accountFilter, providerFilter, sourceFilter, targets],
  )
  const searched = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return filtered
    return filtered.filter((target) =>
      [target.model, target.account.name, target.account.provider].some((value) => value.toLowerCase().includes(query)),
    )
  }, [filtered, search])

  function toggle(target: Target) {
    const key = keyOf(target)
    onChange(selected.includes(key) ? selected.filter((item) => item !== key) : [...selected, key])
  }
  function toggleAllSearched() {
    const keys = searched.map(keyOf)
    const allSelected = keys.length > 0 && keys.every((key) => selected.includes(key))
    onChange(allSelected ? selected.filter((key) => !keys.includes(key)) : [...new Set([...selected, ...keys])])
  }

  return (
    <Dialog title="选择测试模型" className="my-6 max-w-5xl lg:my-[12vh]" onClose={onClose}>
      <div className="flex max-h-[min(680px,calc(100dvh-10rem))] flex-col gap-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="账号来源">
            <Select
              value={sourceFilter}
              onChange={(event) => {
                setSourceFilter(event.target.value as typeof sourceFilter)
                setAccountFilter('all')
              }}
            >
              <option value="all">全部来源</option>
              <option value="upstream">上游账号</option>
              <option value="agent">网关代理</option>
            </Select>
          </Field>
          <Field label="供应商筛选">
            <Select
              value={providerFilter}
              onChange={(event) => {
                setProviderFilter(event.target.value)
                setAccountFilter('all')
              }}
            >
              <option value="all">全部供应商</option>
              {providers.map((provider) => (
                <option key={provider} value={provider}>{provider}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="具体账号">
          <Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}>
            <option value="all">全部账号</option>
            {accountOptions.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} · {account.source === 'agent' ? '网关代理' : '上游'}
              </option>
            ))}
          </Select>
        </Field>
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索模型、账号或供应商" className="pl-9" />
        </div>
        <div className="flex items-center justify-between gap-3 text-xs text-mist">
          <span>搜索结果 {searched.length} 个</span>
          <div className="flex gap-3">
            <button type="button" className="text-signal hover:underline" onClick={toggleAllSearched}>
              {searched.length > 0 && searched.every((target) => selected.includes(keyOf(target))) ? '取消全选' : '全选搜索结果'}
            </button>
            <button type="button" className="hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={() => onChange([])} disabled={!selected.length}>
              清空已选
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto border-y border-line py-2">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {searched.map((target) => {
              const key = keyOf(target)
              const checked = selected.includes(key)
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggle(target)}
                  className={cn(
                    'flex w-full min-w-0 items-center gap-3 border px-3 py-2.5 text-left text-sm transition-colors',
                    checked ? 'border-signal/50 bg-signal/10' : 'border-transparent hover:border-line hover:bg-white/5',
                  )}
                >
                  <span className={cn('flex h-4 w-4 shrink-0 items-center justify-center border text-[10px]', checked ? 'border-signal bg-signal text-black' : 'border-mist/50')}>
                    {checked ? '✓' : null}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-paper" title={target.model}>{target.model}</span>
                    <span className="mt-0.5 block truncate text-xs text-mist" title={target.account.name}>
                      {target.account.name} · {target.account.provider}
                      {target.enabled ? '' : ' · 已关闭'}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
          {!searched.length ? <div className="py-10 text-center text-xs text-mist">没有匹配的模型或账号</div> : null}
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-xs text-signal">{selected.length} 个模型已选</span>
          <Button onClick={onClose}>完成选择</Button>
        </div>
      </div>
    </Dialog>
  )
}
