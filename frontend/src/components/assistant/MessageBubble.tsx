import { Brain, Check, ChevronDown, Copy, Sparkles } from 'lucide-react'
import { useState } from 'react'

import { notifyOk } from '../../lib/toast'
import { cn, copyText } from '../../lib/utils'
import { AssistantMarkdown } from './AssistantMarkdown'
import type { UiMessage } from './AssistantProvider'
import { ToolCallCard } from './ToolCallCard'

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-1" aria-label="正在生成">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-signal [animation-delay:-0.2s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-signal [animation-delay:-0.1s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-signal" />
    </span>
  )
}

function ReasoningBlock({ content, live }: { content: string; live: boolean }) {
  const [open, setOpen] = useState(live)
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className={cn(
        'group/reasoning overflow-hidden rounded-xl border',
        live
          ? 'border-signal/25 bg-gradient-to-br from-signal/8 via-panel/50 to-info/5'
          : 'border-line/70 bg-ink/25',
      )}
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-[11px] text-mist-soft transition-colors hover:text-mist [&::-webkit-details-marker]:hidden">
        <Brain size={12} className={cn(live && 'animate-pulse text-signal')} />
        思考过程
        {live ? <span className="text-signal/80">· 进行中</span> : null}
        <ChevronDown size={12} className="ml-auto transition-transform group-open/reasoning:rotate-180" />
      </summary>
      <div className="max-h-44 overflow-y-auto border-t border-line/50 px-2.5 py-2 text-[11px] leading-5 whitespace-pre-wrap break-words text-mist">
        {content}
      </div>
    </details>
  )
}

export function MessageBubble({
  message,
  onDecideApproval,
}: {
  message: UiMessage
  onDecideApproval: (approvalId: string, decision: 'approve' | 'reject') => void
}) {
  const [copied, setCopied] = useState(false)

  if (message.role === 'user') {
    return (
      <div className="flex min-w-0 justify-end">
        <div className="max-w-[86%] rounded-2xl rounded-br-md bg-signal px-3.5 py-2 text-sm leading-6 text-on-signal shadow-[0_6px_24px_-12px_rgba(200,245,66,0.7)]">
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        </div>
      </div>
    )
  }

  const hasBody = Boolean(message.content.trim())
  const hasTools = message.toolCalls.length > 0
  const hasReasoning = Boolean(message.reasoning.trim())
  const hasSegments = message.segments.length > 0
  const showTyping = message.pending && !hasSegments && !hasReasoning

  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-signal/25 bg-gradient-to-br from-signal/20 via-panel-2 to-info/10 text-signal shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
        <Sparkles size={14} />
      </span>
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            'group relative max-w-full rounded-2xl rounded-tl-md border px-3.5 py-2.5',
            message.error && !hasBody && !hasTools && !hasReasoning
              ? 'border-danger/30 bg-danger/10'
              : 'border-line/80 bg-panel-2/75 shadow-[0_10px_30px_-18px_rgba(0,0,0,0.6)]',
          )}
        >
          {message.segments.length ? (
            <div className="grid gap-1">
              {message.segments.map((segment, index) => {
                if (segment.type === 'reasoning') {
                  return (
                    <ReasoningBlock
                      key={`reasoning-${index}`}
                      content={segment.content}
                      live={message.pending && index === message.segments.length - 1}
                    />
                  )
                }
                if (segment.type === 'text') {
                  return <AssistantMarkdown key={`text-${index}`} content={segment.content} />
                }
                const call = message.toolCalls.find((item) => item.id === segment.id)
                return call ? (
                  <ToolCallCard key={call.id} call={call} onDecideApproval={onDecideApproval} />
                ) : null
              })}
            </div>
          ) : hasBody ? (
            <AssistantMarkdown content={message.content} />
          ) : null}
          {showTyping ? <TypingDots /> : null}
          {!message.segments.length && hasTools ? (
            <div className="mt-1 grid gap-2">
              {message.toolCalls.map((call) => (
                <ToolCallCard key={call.id} call={call} onDecideApproval={onDecideApproval} />
              ))}
            </div>
          ) : null}
          {message.error ? (
            <div
              className={cn(
                'mt-1.5 text-xs leading-5',
                hasBody || hasTools || hasReasoning
                  ? 'rounded-lg border border-danger/25 bg-danger/5 px-2.5 py-1.5 text-danger'
                  : 'text-danger',
              )}
            >
              {message.error}
            </div>
          ) : null}
          {hasBody ? (
            <button
              type="button"
              className="absolute -right-1 -top-3 hidden h-7 w-7 items-center justify-center rounded-full border border-line bg-panel text-mist shadow-menu transition-colors hover:text-paper group-hover:flex md:h-6 md:w-6"
              aria-label="复制回复"
              title="复制回复"
              onClick={async () => {
                try {
                  await copyText(message.content)
                  setCopied(true)
                  notifyOk('已复制')
                  window.setTimeout(() => setCopied(false), 1500)
                } catch {
                  notifyOk('复制失败')
                }
              }}
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
