import { useEffect, useRef } from 'react'
import type { LogItem, LogMessageItem } from '../lib/api'
import { buildTurns, isLongText, type ConversationMessage } from '../lib/logView'
import { cn } from '../lib/utils'
import { Badge } from './ui'
import { CollapsibleText, CopyButton, ReasoningBlock, ToolCallList } from './LogsShared'

function HighlightedText({ text, start, end }: { text: string; start: number; end: number }) {
  if (start < 0 || end <= start || start >= text.length) return <>{text}</>
  const safeEnd = Math.min(end, text.length)
  return (
    <>
      {text.slice(0, start)}
      <mark className="rounded-sm bg-warn/40 text-paper">{text.slice(start, safeEnd)}</mark>
      {text.slice(safeEnd)}
    </>
  )
}

function roleMeta(role: string) {
  if (role === 'assistant') return { label: '模型', tone: 'ok' as const }
  if (role === 'user') return { label: '用户', tone: 'info' as const }
  if (role === 'system') return { label: '系统', tone: 'mist' as const }
  if (role === 'tool') return { label: '工具', tone: 'warn' as const }
  return { label: role, tone: 'mist' as const }
}

function MessageBubble({
  message,
  active,
  highlight,
}: {
  message: ConversationMessage
  active: boolean
  highlight: { start: number; end: number } | null
}) {
  const assistant = message.role === 'assistant'
  const meta = roleMeta(message.role)
  const long = isLongText(message.text)
  return (
    <div
      id={active ? 'audit-hit' : undefined}
      data-seq={message.seq}
      className={cn('min-w-0 max-w-full scroll-mt-24', assistant ? 'lg:pl-8' : 'lg:pr-8')}
    >
      <div className="mb-1 flex items-center gap-2 text-xs text-mist">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <span className="font-mono text-[10px] text-mist/70">#{message.seq}</span>
      </div>
      <div
        className={cn(
          'min-w-0 max-w-full overflow-hidden rounded-2xl p-4',
          assistant ? 'rounded-tl-sm border border-line bg-panel' : 'rounded-tr-sm bg-signal/10',
          active && 'ring-2 ring-warn/70',
        )}
      >
        <div className="max-w-full overflow-x-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
          {active && highlight ? (
            <HighlightedText text={message.text} start={highlight.start} end={highlight.end} />
          ) : (
            <CollapsibleText text={message.text} forceExpanded={active && long} />
          )}
        </div>
        <ReasoningBlock items={message.reasoning} />
        <ToolCallList calls={message.toolCalls} />
      </div>
    </div>
  )
}

function scrollNodeIntoMain(node: HTMLElement) {
  // scrollIntoView 会同时处理桌面端 main 滚动容器与移动端 window 滚动；
  // 气泡上的 scroll-mt-24 负责给固定顶栏留出偏移。
  node.scrollIntoView({ block: 'start', behavior: 'auto' })
}

export function LogConversation({
  log,
  messages,
  targetSeq,
  highlight,
  onLocated,
}: {
  log: LogItem
  messages: LogMessageItem[]
  targetSeq: number | null
  highlight: { start: number; end: number } | null
  onLocated?: () => void
}) {
  const reasoning = log.reasoning ?? {}
  const turns = buildTurns(messages, reasoning)
  const scrolledRef = useRef(false)

  useEffect(() => {
    scrolledRef.current = false
  }, [log.id, targetSeq])

  useEffect(() => {
    if (targetSeq == null || scrolledRef.current) return
    const node = document.getElementById('audit-hit')
    if (!node) return
    scrolledRef.current = true
    scrollNodeIntoMain(node)
    onLocated?.()
  }, [targetSeq, messages, onLocated])

  const plain = turns
    .flatMap((turn) => turn.messages)
    .map((message) => `${message.role}: ${message.text}`)
    .join('\n\n')

  if (messages.length === 0) {
    return <div className="px-4 py-10 text-center text-sm text-mist">这次会话没有记录到消息内容。</div>
  }

  return (
    <div className="min-w-0 space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs text-mist">
          {turns.length} 轮 · {messages.length} 条消息
        </div>
        <CopyButton value={plain} label="复制对话" />
      </div>
      {turns.map((turn) => (
        <div key={turn.key} className="min-w-0 space-y-3">
          {turn.index != null && turns.length > 1 ? (
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-mist">第 {turn.index} 轮</span>
              <span className="h-px flex-1 bg-line" />
            </div>
          ) : null}
          {turn.messages.map((message) => (
            <MessageBubble
              key={message.seq}
              message={message}
              active={targetSeq != null && message.seq === targetSeq}
              highlight={targetSeq != null && message.seq === targetSeq ? highlight : null}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
