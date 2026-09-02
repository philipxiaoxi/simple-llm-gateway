import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Button, Field, Input, Select } from './ui'
import type { ShareAlias } from '../lib/api'
import { notifyBad } from '../lib/toast'
import { ALIAS_INPUT_PATTERN, cn } from '../lib/utils'

export function ModelAliasList({
  aliases,
  models,
  busy = false,
  onSwitch,
  onRename,
  onDelete,
}: {
  aliases: ShareAlias[]
  models: string[]
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
        <table className="w-full text-left text-sm">
          <thead className="bg-panel-2 text-mist">
            <tr>
              <th className="whitespace-nowrap px-3 py-2 font-medium">别名</th>
              <th className="px-3 py-2 font-medium">指向模型</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {aliases.map((item) => {
              const targetMissing = !models.includes(item.model)
              const isRenaming = renamingAlias === item.alias
              return (
                <tr key={item.alias} className="border-t border-line hover:bg-white/5">
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
                      className={cn('w-full', targetMissing && 'border-warn/60 text-warn')}
                      value={targetMissing ? '' : item.model}
                      disabled={busy}
                      onChange={(event) => onSwitch(item.alias, event.target.value)}
                    >
                      {targetMissing ? <option value="">模型已移除，请重新选择</option> : null}
                      {models.map((model) => (
                        <option key={model} value={model}>
                          {model}
                        </option>
                      ))}
                    </Select>
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

      <div className="grid gap-2 lg:hidden">
        {aliases.map((item) => {
          const targetMissing = !models.includes(item.model)
          const isRenaming = renamingAlias === item.alias
          return (
            <div key={item.alias} className="space-y-3 rounded-lg border border-line bg-ink/40 p-3">
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
                  className={cn(targetMissing && 'border-warn/60 text-warn')}
                  value={targetMissing ? '' : item.model}
                  disabled={busy}
                  onChange={(event) => onSwitch(item.alias, event.target.value)}
                >
                  {targetMissing ? <option value="">模型已移除，请重新选择</option> : null}
                  {models.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          )
        })}
      </div>
    </>
  )
}
