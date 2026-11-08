import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Badge, Button, Field, Input, Select } from './ui'
import type { ShareAlias, ShareModelEntry } from '../lib/api'
import { accountColor } from '../lib/accountModels'
import { modelOptions } from '../lib/modelOptions'
import { notifyBad } from '../lib/toast'
import { ALIAS_INPUT_PATTERN, cn } from '../lib/utils'

function AccountCell({ alias }: { alias: ShareAlias }) {
  const available = alias.available ?? true
  const color = accountColor(alias.account_index ?? 0)
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {alias.account_name ? (
        <>
          <span className={cn('size-2 shrink-0 rounded-full', color.dot)} aria-hidden="true" />
          <span className="min-w-0 max-w-48 truncate text-sm text-mist" title={alias.account_name}>
            {alias.account_name}
          </span>
        </>
      ) : (
        <span className="text-sm text-mist-soft">—</span>
      )}
      {available ? null : <Badge tone="warn">已失效</Badge>}
    </div>
  )
}

export function ModelAliasList({
  aliases,
  models,
  modelEntries,
  busy = false,
  onSwitch,
  onRename,
  onDelete,
}: {
  aliases: ShareAlias[]
  models: string[]
  modelEntries?: ShareModelEntry[]
  busy?: boolean
  onSwitch: (alias: string, model: string) => void
  onRename: (oldAlias: string, newAlias: string) => void
  onDelete: (alias: string) => void
}) {
  const [renamingAlias, setRenamingAlias] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')

  function startRename(alias: string) {
    setRenamingAlias(alias)
    setRenameDraft(alias)
  }

  function submitRename() {
    const oldAlias = renamingAlias
    if (!oldAlias) return
    const newAlias = renameDraft.trim()
    if (!ALIAS_INPUT_PATTERN.test(newAlias)) {
      notifyBad('别名仅允许字母、数字和 . _ / -，以字母或数字开头，最长 64 位。')
      return
    }
    if (newAlias === oldAlias) {
      setRenamingAlias(null)
      return
    }
    if (aliases.some((item) => item.alias === newAlias)) {
      notifyBad(`别名 ${newAlias} 已存在。`)
      return
    }
    onRename(oldAlias, newAlias)
    setRenamingAlias(null)
  }

  return (
    <>
      <div className="hidden overflow-hidden rounded-lg border border-line bg-ink/40 lg:block">
        <table className="w-full table-fixed text-left text-sm">
          <colgroup>
            <col className="w-40" />
            <col />
            <col className="w-28" />
          </colgroup>
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="whitespace-nowrap px-3 py-2 font-medium">别名</th>
              <th className="px-3 py-2 font-medium">指向模型 / 上游账号</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {aliases.map((item) => {
              const available = item.available ?? models.includes(item.model)
              const isRenaming = renamingAlias === item.alias
              return (
                <tr
                  key={item.alias}
                  className={cn('border-t border-line hover:bg-elevate', available ? null : 'bg-warn/5')}
                >
                  <td className="max-w-44 truncate px-3 py-2 font-mono text-sm text-paper" title={item.alias}>
                    {isRenaming ? (
                      <Input
                        value={renameDraft}
                        autoComplete="off"
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        onChange={(event) => setRenameDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault()
                            submitRename()
                          }
                          if (event.key === 'Escape') setRenamingAlias(null)
                        }}
                      />
                    ) : (
                      item.alias
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Select
                      className={cn('w-full', available ? null : 'border-warn/60 text-warn')}
                      value={item.model}
                      disabled={busy}
                      onChange={(event) => onSwitch(item.alias, event.target.value)}
                    >
                      {available ? null : (
                        <optgroup label="已失效">
                          <option value={item.model} disabled>
                            {item.model}（已失效）
                          </option>
                        </optgroup>
                      )}
                      {modelOptions(models, modelEntries)}
                    </Select>
                    <div className="mt-1.5">
                      <AccountCell alias={item} />
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <div className="flex items-center justify-end gap-2">
                      {isRenaming ? (
                        <>
                          <Button type="button" variant="ghost" className="px-2" disabled={busy} onClick={() => setRenamingAlias(null)}>
                            取消
                          </Button>
                          <Button type="button" className="px-2" disabled={busy} onClick={submitRename}>
                            保存
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button type="button" variant="ghost" className="size-9 px-0" disabled={busy} title={`重命名别名 ${item.alias}`} onClick={() => startRename(item.alias)}>
                            <Pencil size={15} />
                          </Button>
                          <Button
                            type="button"
                            variant="danger"
                            className="size-9 px-0"
                            disabled={busy}
                            title={`删除别名 ${item.alias}`}
                            onClick={() => onDelete(item.alias)}
                          >
                            <Trash2 size={15} />
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="grid min-w-0 gap-2 lg:hidden">
        {aliases.map((item) => {
          const available = item.available ?? models.includes(item.model)
          const isRenaming = renamingAlias === item.alias
          return (
            <div
              key={item.alias}
              className={cn(
                'min-w-0 space-y-3 rounded-lg border border-line bg-ink/40 p-3',
                available ? null : 'border-warn/60 bg-warn/5',
              )}
            >
              <div className="flex items-center gap-2">
                {isRenaming ? (
                  <Input
                    value={renameDraft}
                    autoComplete="off"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault()
                        submitRename()
                      }
                      if (event.key === 'Escape') setRenamingAlias(null)
                    }}
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-paper" title={item.alias}>
                    {item.alias}
                  </span>
                )}
                <div className="flex shrink-0 gap-2">
                  {isRenaming ? (
                    <>
                      <Button type="button" variant="ghost" className="px-2" disabled={busy} onClick={() => setRenamingAlias(null)}>
                        取消
                      </Button>
                      <Button type="button" className="px-2" disabled={busy} onClick={submitRename}>
                        保存
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button type="button" variant="ghost" className="size-9 px-0" disabled={busy} title={`重命名别名 ${item.alias}`} onClick={() => startRename(item.alias)}>
                        <Pencil size={15} />
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        className="size-9 px-0"
                        disabled={busy}
                        title={`删除别名 ${item.alias}`}
                        onClick={() => onDelete(item.alias)}
                      >
                        <Trash2 size={15} />
                      </Button>
                    </>
                  )}
                </div>
              </div>
              <Field label="指向模型">
                <Select
                  className={cn(available ? null : 'border-warn/60 text-warn')}
                  value={item.model}
                  disabled={busy}
                  onChange={(event) => onSwitch(item.alias, event.target.value)}
                >
                  {available ? null : (
                    <optgroup label="已失效">
                      <option value={item.model} disabled>
                        {item.model}（已失效）
                      </option>
                    </optgroup>
                  )}
                  {modelOptions(models, modelEntries)}
                </Select>
              </Field>
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="shrink-0 text-xs text-mist-soft">上游账号</span>
                <AccountCell alias={item} />
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
