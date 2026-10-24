import { useState } from 'react'
import { Download } from 'lucide-react'
import { api, type LogItem, type LogMessageItem } from '../lib/api'
import { Badge, Button } from './ui'
import { CopyButton } from './LogsShared'
import { errorMessage } from '../lib/utils'
import { notifyBad, notifyOk } from '../lib/toast'
import { useCan } from '../lib/session'

function JsonSection({
  title,
  hint,
  value,
  defaultOpen = false,
}: {
  title: string
  hint?: string
  value: string
  defaultOpen?: boolean
}) {
  return (
    <details open={defaultOpen} className="rounded-xl border border-line bg-panel/60">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3">
        <span className="text-sm font-medium">{title}</span>
        {hint ? <span className="text-xs text-mist">{hint}</span> : null}
        <span className="ml-auto">
          <CopyButton value={value} />
        </span>
      </summary>
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-all border-t border-line bg-ink/70 p-4 font-mono text-[11px] text-mist [overflow-wrap:anywhere]">
        {value}
      </pre>
    </details>
  )
}

function downloadJson(payload: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function LogContent({ log, messages }: { log: LogItem; messages: LogMessageItem[] }) {
  const canExport = useCan('logs:export')
  const [exporting, setExporting] = useState(false)
  const meta = {
    id: log.id,
    account_id: log.account_id,
    account_name: log.account_name,
    account_source: log.account_source,
    api_key_id: log.api_key_id,
    api_key_name: log.api_key_name,
    protocol: log.protocol,
    model: log.model,
    model_chain: log.model_chain ?? null,
    stream: log.stream,
    status: log.status,
    http_status: log.http_status,
    error_message: log.error_message,
    prompt_tokens: log.prompt_tokens,
    completion_tokens: log.completion_tokens,
    total_tokens: log.total_tokens,
    latency_ms: log.latency_ms,
    message_count: log.message_count,
    turn_count: log.turn_count,
    storage_truncated: log.storage_truncated ?? false,
    created_at: log.created_at,
    updated_at: log.updated_at,
  }

  const handleDownload = async () => {
    if (exporting) return
    setExporting(true)
    try {
      const payload = await api.logExport(log.id)
      downloadJson(
        {
          meta,
          messages: payload.messages,
          reasoning: payload.reasoning,
        },
        `log-${log.id}-${new Date().toISOString().slice(0, 10)}.json`,
      )
      notifyOk(`已导出 ${payload.messages.length} 条消息`)
    } catch (caught) {
      notifyBad(errorMessage(caught, '导出失败'))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel/60 px-4 py-3 text-xs text-mist">
        <Badge tone="info">说明</Badge>
        本站网关出于隐私考虑不落盘原始 HTTP 请求/响应报文，这里展示的是网关保存的会话消息、思考内容与统计元数据。
      </div>
      <JsonSection
        title="会话元数据"
        hint="session metadata"
        value={JSON.stringify(meta, null, 2)}
        defaultOpen
      />
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel/60 px-4 py-3">
        <div className="min-w-0 flex-1 text-xs text-mist">
          会话消息与思考内容不再内联渲染（避免超长会话拖慢页面），可下载完整 JSON 后本地查看。
          {messages.length < (log.message_count ?? 0) ? `当前页已加载 ${messages.length} 条，导出为全量。` : null}
        </div>
        {canExport ? (
        <Button disabled={exporting} onClick={() => void handleDownload()}>
          <Download size={14} className={exporting ? 'animate-pulse' : undefined} />
          {exporting ? '导出中…' : '下载完整 JSON'}
        </Button>
        ) : null}
      </div>
    </div>
  )
}
