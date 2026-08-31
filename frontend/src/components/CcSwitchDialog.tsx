import { useState } from 'react'
import { Button, Dialog, Field, Select } from './ui'

export type CcSwitchValues = {
  model: string
  haiku: string
  sonnet: string
  opus: string
}

export type CcSwitchModel = {
  id: string
  accountName?: string
  accountIndex?: number
}

const CLAUDE_MODEL_SLOTS = ['haiku', 'sonnet', 'opus'] as const

export function CcSwitchDialog({
  label,
  models,
  isClaude,
  initial,
  pending,
  onConfirm,
  onClose,
}: {
  label: string
  models: Array<CcSwitchModel | string>
  isClaude: boolean
  initial: CcSwitchValues
  pending?: boolean
  onConfirm: (values: CcSwitchValues) => void
  onClose: () => void
}) {
  const [values, setValues] = useState<CcSwitchValues>(initial)
  const normalizedModels = models.map((model) => (typeof model === 'string' ? { id: model } : model))
  const groups = normalizedModels.reduce<CcSwitchModel[][]>((items, model) => {
    const index = model.accountIndex ?? 0
    if (!items[index]) items[index] = []
    items[index].push(model)
    return items
  }, [])

  function modelOptions() {
    return groups.map((items, index) =>
      items?.length ? (
        <optgroup key={index} label={`优先 ${index + 1} · ${items[0].accountName ?? '可用模型'}`}>
          {items.map((model) => (
            <option key={model.id} value={model.id}>
              {model.id}
            </option>
          ))}
        </optgroup>
      ) : null,
    )
  }

  return (
    <Dialog title={`导入到 ${label}`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm text-mist">模型来自该 Key 绑定的上游账号，请按 {label} 的角色选好再导入。</p>
        <Field label={isClaude ? '主模型' : '模型'}>
          <Select
            value={values.model}
            onChange={(event) => setValues({ ...values, model: event.target.value })}
          >
            {modelOptions()}
          </Select>
        </Field>
        {isClaude
          ? CLAUDE_MODEL_SLOTS.map((slot) => (
              <Field key={slot} label={`${slot[0].toUpperCase()}${slot.slice(1)} 模型（可选）`}>
                <Select
                  value={values[slot]}
                  onChange={(event) => setValues({ ...values, [slot]: event.target.value })}
                >
                  <option value="">不设置</option>
                  {modelOptions()}
                </Select>
              </Field>
            ))
          : null}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => onConfirm(values)}>
            打开 CC Switch
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
