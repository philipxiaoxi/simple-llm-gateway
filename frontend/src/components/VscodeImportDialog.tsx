import { notifyBad, notifyOk } from '../lib/toast'
import { formatContextWindow } from '../lib/utils'
import { ModelPickDialog } from './ModelPickDialog'
import { Button } from './ui'

type VscodeModel = {
  id: string
  name?: string
  url?: string
  toolCalling?: boolean
  vision?: boolean
  maxInputTokens?: number
  maxOutputTokens?: number
}

type VscodeConfig = {
  name?: string
  vendor?: string
  apiKey?: string
  apiType?: string
  models?: VscodeModel[]
  [key: string]: unknown
}

function modelHint(model: VscodeModel) {
  const extras = [
    model.maxInputTokens ? `上下文 ${formatContextWindow(model.maxInputTokens)}` : null,
    model.maxOutputTokens ? `输出 ${formatContextWindow(model.maxOutputTokens)}` : null,
    model.vision ? '视觉' : null,
  ].filter(Boolean)
  return extras.join(' · ')
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function VscodeImportDialog({
  config,
  onClose,
}: {
  config: Record<string, unknown>
  onClose: () => void
}) {
  const vscode = config as VscodeConfig
  const models = (Array.isArray(vscode.models) ? vscode.models.filter((item) => item?.id) : []).map((item) => ({
    id: item.id,
    hint: modelHint(item),
  }))

  function buildText(selectedIds: string[]) {
    const chosenIds = new Set(selectedIds)
    const chosen = (vscode.models ?? []).filter((item) => chosenIds.has(item.id))
    return JSON.stringify({ ...vscode, models: chosen }, null, 2)
  }

  function downloadSelected(selectedIds: string[]) {
    if (!selectedIds.length) {
      notifyBad('请至少选择一个模型')
      return
    }
    downloadText('chatLanguageModels.json', buildText(selectedIds))
    notifyOk('已下载 chatLanguageModels.json')
  }

  return (
    <ModelPickDialog
      title="导入到 VSCode"
      description="模型太多时整包复制容易失败。勾选你要用的模型，再复制到 chatLanguageModels.json。"
      models={models}
      confirmLabel="复制所选"
      successMessage={(count) => `已复制 ${count} 个模型的 VSCode 配置，粘贴到 chatLanguageModels.json 即可。`}
      extraActions={(selectedIds) => (
        <Button type="button" variant="line" onClick={() => downloadSelected(selectedIds)}>
          下载 JSON
        </Button>
      )}
      buildText={buildText}
      onClose={onClose}
    />
  )
}
