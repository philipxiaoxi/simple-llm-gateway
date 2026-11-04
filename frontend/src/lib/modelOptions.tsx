import type { ReactNode } from 'react'

import type { ShareModelEntry } from './api'

type ModelGroupOption = { key: string; label: string; models: string[] }

function groupModels(models: string[], entries: ShareModelEntry[]): ModelGroupOption[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]))
  const groups: ModelGroupOption[] = []
  const byKey = new Map<string, ModelGroupOption>()
  for (const model of models) {
    const entry = byId.get(model)
    const key = entry ? `${entry.account_index}:${entry.account_name}` : ''
    let group = byKey.get(key)
    if (!group) {
      group = { key: key || 'ungrouped', label: entry?.account_name ?? '', models: [] }
      byKey.set(key, group)
      groups.push(group)
    }
    group.models.push(model)
  }
  return groups
}

/**
 * 模型下拉选项：有账号信息时按上游账号分组，避免同名/多账号时不知道模型属于哪家。
 * 返回元素数组而非组件，Select 只解析直接的 option/optgroup 子元素。
 */
export function modelOptions(models: string[], modelEntries?: ShareModelEntry[]) {
  const nodes: ReactNode[] = []
  for (const group of groupModels(models, modelEntries ?? [])) {
    const options = group.models.map((model) => (
      <option key={model} value={model}>
        {model}
      </option>
    ))
    if (group.label) {
      nodes.push(
        <optgroup key={group.key} label={group.label}>
          {options}
        </optgroup>,
      )
    } else {
      nodes.push(...options)
    }
  }
  return nodes
}
