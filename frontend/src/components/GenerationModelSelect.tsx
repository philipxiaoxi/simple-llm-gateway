import type { GenerationModel } from '../lib/api'
import { groupModelsByProvider, modelKey } from '../lib/generation'
import { Select } from './ui'

/**
 * 生成模型下拉框。
 *
 * 目录里同时包含多家供应商的模型（同名模型可能两家都有），所以：
 * - 按供应商分组渲染，值统一用 `provider:model`，避免同名模型互相串台；
 * - **选项文案带上供应商**。原生 select 收起时只显示选中项的文本，不带供应商的话
 *   收起来就只剩 `nano-banana-2`，看不出是哪家的（分组只在展开时可见）。
 */
export function GenerationModelSelect({
  models,
  value,
  onChange,
  disabled = false,
}: {
  models: GenerationModel[]
  value: string
  onChange: (value: string) => void
  disabled?: boolean
}) {
  return (
    <Select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
      {groupModelsByProvider(models).map((group) => (
        <optgroup key={group.provider} label={group.label}>
          {group.models.map((model) => (
            <option key={modelKey(model)} value={modelKey(model)}>
              {model.provider} · {model.label}
            </option>
          ))}
        </optgroup>
      ))}
    </Select>
  )
}

export default GenerationModelSelect
