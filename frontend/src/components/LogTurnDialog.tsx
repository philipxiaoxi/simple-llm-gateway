import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api, type LogItem, type LogMessageItem } from '../lib/api'
import { asText, normalizeToolCalls, type ConversationMessage } from '../lib/logView'
import { cn } from '../lib/utils'
import { errorMessage } from '../lib/utils'
import { notifyBad } from '../lib/toast'
import { Button } from './ui'
import { MessageBubble } from './LogConversation'

const TURN_PAGE_SIZE = 200

function fullText(item: LogMessageItem): string {
  if (item.content != null) return asText(item.content)
  return item.content_preview ?? ''
}

function toFullMessage(item: LogMessageItem, log: LogItem): ConversationMessage {
  const toolCalls = normalizeToolCalls(item.tool_calls_full ?? item.tool_calls)
  const summary = log.reasoning_summary ?? {}
  const reasoning = toolCalls
    .filter((call) => summary[call.id])
    .map((call) => ({ id: call.id, length: summary[call.id].length, preview: summary[call.id].preview }))
  return {
    seq: item.seq,
    role: item.role || 'user',
    text: fullText(item),
    toolCalls,
    reasoning,
  }
}

export function LogTurnDialog({
  log,
  anchor,
  to,
  index,
  onClose,
}: {
  log: LogItem
  anchor: number
  to: number | null
  index: number | null
  onClose: () => void
}) {
  const [items, setItems] = useState<LogMessageItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setItems(null)
    setFailed(false)
    setTotal(0)
    api
      .logMessages(log.id, 1, TURN_PAGE_SIZE, { fromSeq: anchor, toSeq: to ?? undefined, detail: true })
      .then((payload) => {
        if (cancelled) return
        setItems(payload.items.sort((left, right) => left.seq - right.seq))
        setTotal(payload.total)
      })
      .catch((caught) => {
        if (cancelled) return
        setFailed(true)
        notifyBad(errorMessage(caught, '轮次内容加载失败'))
      })
    return () => {
      cancelled = true
    }
  }, [log.id, anchor, to])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const messages = useMemo(
    () => (items ?? []).map((item) => toFullMessage(item, log)),
    [items, log],
  )
  const title = index != null ? `第 ${index} 轮预览` : '上下文预览'
  const range = to != null ? `seq ${anchor}–${to}` : `seq ≥ ${anchor}`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="关闭弹窗"
        className="absolute inset-0 cursor-default bg-black/60"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        className={cn('relative flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-ink shadow-2xl')}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-paper">{title}</div>
            <div className="mt-0.5 font-mono text-[11px] text-mist">
              {range} · {total} 条消息
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-mist transition hover:bg-white/5 hover:text-paper"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {items === null && !failed ? (
            <div className="px-4 py-12 text-center text-sm text-mist">加载轮次内容…</div>
          ) : failed ? (
            <div className="px-4 py-12 text-center text-sm text-mist">轮次内容加载失败</div>
          ) : messages.length === 0 ? (
            <div className="px-4 py-12 text-center text-sm text-mist">该轮没有记录到消息内容。</div>
          ) : (
            <>
              {total > (items?.length ?? 0) ? (
                <div className="rounded-xl border border-warn/40 bg-warn/[0.06] px-4 py-2.5 text-xs text-warn">
                  该轮共 {total} 条消息，仅展示最近的 {items?.length ?? 0} 条。
                </div>
              ) : null}
              {messages.map((message) => (
                <MessageBubble key={message.seq} message={message} logId={log.id} />
              ))}
            </>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
          <Button variant="line" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </div>
  )
}
