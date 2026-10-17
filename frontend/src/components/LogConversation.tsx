import { useMemo, useState } from 'react'
import type { LogItem, LogMessageItem, LogTurn } from '../lib/api'
import { buildTurns, summarizeTurn, type ConversationMessage, type ConversationTurn } from '../lib/logView'
import { cn } from '../lib/utils'
import { Badge, Button } from './ui'
import { CollapsibleText, CopyButton, ReasoningBlock, ToolCallList } from './LogsShared'

const DEFAULT_EXPANDED_TURNS = 2

function roleMeta(role: string) {
  if (role === 'assistant') return { label: '模型', tone: 'ok' as const }
  if (role === 'user') return { label: '用户', tone: 'info' as const }
  if (role === 'system') return { label: '系统', tone: 'mist' as const }
  if (role === 'tool') return { label: '工具', tone: 'warn' as const }
  return { label: role, tone: 'mist' as const }
}

export function MessageBubble({ message, logId }: { message: ConversationMessage; logId: number }) {
  const assistant = message.role === 'assistant'
  const meta = roleMeta(message.role)
  return (
    <div data-seq={message.seq} className={cn('min-w-0 max-w-full', assistant ? 'lg:pl-8' : 'lg:pr-8')}>
      <div className="mb-1 flex items-center gap-2 text-xs text-mist">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <span className="font-mono text-[10px] text-mist/70">#{message.seq}</span>
      </div>
      <div
        className={cn(
          'min-w-0 max-w-full overflow-hidden rounded-2xl p-4',
          assistant ? 'rounded-tl-sm border border-line bg-panel' : 'rounded-tr-sm bg-signal/10',
        )}
      >
        <div className="max-w-full overflow-x-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
          <CollapsibleText text={message.text} />
        </div>
        <ReasoningBlock logId={logId} items={message.reasoning} />
        <ToolCallList calls={message.toolCalls} />
      </div>
    </div>
  )
}

function TurnLabel({ index, hint }: { index: number | null; hint?: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-mist">
        {index != null ? `第 ${index} 轮` : '上下文'}
      </span>
      {hint ? <span className="text-[11px] text-mist/60">{hint}</span> : null}
      <span className="h-px flex-1 bg-line" />
    </div>
  )
}

function TurnSummaryRow({
  turn,
  index,
  onExpand,
  onPreview,
}: {
  turn: ConversationTurn
  index: number | null
  onExpand: () => void
  onPreview: () => void
}) {
  const summary = summarizeTurn(turn)
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-panel/40 px-4 py-3">
      <Badge tone="mist">{index != null ? `第 ${index} 轮` : '上下文'}</Badge>
      <div className="min-w-0 flex-1 truncate text-sm text-mist" title={summary.preview}>
        {summary.preview}
      </div>
      <span className="shrink-0 text-xs text-mist/70">
        {turn.messages.length} 条 · {summary.textLength} 字
        {summary.reasoningCount ? ` · 思考 ${summary.reasoningCount}` : ''}
        {summary.toolCount ? ` · 工具 ${summary.toolCount}` : ''}
      </span>
      <Button variant="line" className="shrink-0" onClick={onExpand}>
        展开
      </Button>
      <Button variant="line" className="shrink-0" onClick={onPreview}>
        预览
      </Button>
    </div>
  )
}

function UnloadedSummaryRow({
  turnCount,
  messageCount,
  loading,
  onLoad,
}: {
  turnCount: number
  messageCount: number
  loading: boolean
  onLoad: () => void
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-panel/40 px-4 py-3">
      <Badge tone="mist">更早</Badge>
      <div className="min-w-0 flex-1 text-sm text-mist">
        {turnCount > 0 ? `还有 ${turnCount} 轮 · 约 ${messageCount} 条消息未加载` : '更早的消息未加载'}
      </div>
      <Button variant="line" className="shrink-0" disabled={loading} onClick={onLoad}>
        {loading ? '加载中…' : '加载更早'}
      </Button>
    </div>
  )
}

export function LogConversation({
  log,
  messages,
  turnsOutline,
  onPreviewTurn,
  onLoadOlder,
  loadingOlder,
  hasOlder,
}: {
  log: LogItem
  messages: LogMessageItem[]
  turnsOutline: LogTurn[]
  onPreviewTurn: (anchor: number, to: number | null, index: number | null) => void
  onLoadOlder: () => void
  loadingOlder: boolean
  hasOlder: boolean
}) {
  const loadedTurns = useMemo(() => buildTurns(messages, log.reasoning_summary ?? {}), [messages, log.reasoning_summary])
  const loadedByAnchor = useMemo(() => new Map(loadedTurns.map((turn) => [turn.messages[0]?.seq, turn])), [loadedTurns])
  const outlineAnchors = useMemo(() => new Set(turnsOutline.map((turn) => turn.anchor_seq)), [turnsOutline])
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(() => new Set())
  const lastKeys = useMemo(
    () => new Set(loadedTurns.slice(-DEFAULT_EXPANDED_TURNS).map((turn) => `turn-${turn.messages[0]?.seq ?? 0}`)),
    [loadedTurns],
  )
  const collapsedLoadedCount = loadedTurns.filter(
    (turn) => !lastKeys.has(`turn-${turn.messages[0]?.seq ?? 0}`) && !expandedKeys.has(`turn-${turn.messages[0]?.seq ?? 0}`),
  ).length
  // 已加载但不在轮廓内的片段（首条为 assistant 的残段），单独展示避免丢内容
  const orphanTurns = loadedTurns.filter((turn) => turn.messages[0] && !outlineAnchors.has(turn.messages[0].seq))
  // 最新在顶部：轮次倒序展示；未加载的更早轮次聚合为底部一行
  const displayTurns = useMemo(() => [...turnsOutline].reverse(), [turnsOutline])
  const unloadedTurns = displayTurns.filter((turn) => !loadedByAnchor.has(turn.anchor_seq))
  const unloadedMessageCount = unloadedTurns.reduce((sum, turn) => sum + turn.message_count, 0)

  const expandTurn = (key: string) => {
    setExpandedKeys((previous) => {
      const next = new Set(previous)
      next.add(key)
      return next
    })
  }

  const expandAll = () => {
    setExpandedKeys((previous) => {
      const next = new Set(previous)
      for (const turn of loadedTurns) next.add(`turn-${turn.messages[0]?.seq ?? 0}`)
      return next
    })
  }

  const plain = loadedTurns
    .flatMap((turn) => turn.messages)
    .map((message) => `${message.role}: ${message.text}`)
    .join('\n\n')

  return (
    <div className="min-w-0 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="text-xs text-mist">
            {turnsOutline.length} 轮 · {messages.length} 条消息 · 最新在前
          </div>
          {collapsedLoadedCount > 0 ? (
            <button type="button" className="text-xs text-signal hover:underline" onClick={expandAll}>
              全部展开
            </button>
          ) : null}
        </div>
        <CopyButton value={plain} label="复制对话" />
      </div>

      {log.storage_truncated ? (
        <div className="rounded-xl border border-warn/40 bg-warn/[0.06] px-4 py-2.5 text-xs text-warn">
          该会话消息数已达单会话保留上限（1000 条），较早的普通消息已滚动清理，保留头部系统消息与最新消息。
        </div>
      ) : null}

      {turnsOutline.length === 0 && orphanTurns.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-mist">这次会话没有记录到消息内容。</div>
      ) : (
        <div className="min-w-0 space-y-3">
          {displayTurns.map((turn) => {
            const key = `turn-${turn.anchor_seq}`
            const loadedTurn = loadedByAnchor.get(turn.anchor_seq)
            if (!loadedTurn) {
              return null
            }
            const collapsed = !lastKeys.has(key) && !expandedKeys.has(key)
            if (collapsed) {
              return (
                <TurnSummaryRow
                  key={key}
                  turn={loadedTurn}
                  index={turn.index}
                  onExpand={() => expandTurn(key)}
                  onPreview={() => onPreviewTurn(turn.anchor_seq, turn.to_seq, turn.index)}
                />
              )
            }
            return (
              <div key={key} className="min-w-0 space-y-3">
                {turn.index != null || turnsOutline.length > 1 ? <TurnLabel index={turn.index} /> : null}
                {loadedTurn.messages.map((message) => (
                  <MessageBubble key={message.seq} message={message} logId={log.id} />
                ))}
              </div>
            )
          })}
          {orphanTurns.map((turn) => {
            const fragmentSeq = turn.messages[0]?.seq ?? 0
            // 片段归属的轮次：outline 中锚点小于片段首条的最后一轮
            const parent = turnsOutline.filter((item) => item.anchor_seq < fragmentSeq).at(-1)
            return (
              <div key={turn.key} className="min-w-0 space-y-3">
                <TurnLabel
                  index={parent?.index ?? null}
                  hint={parent ? '部分加载，点击底部「加载更早」查看完整轮次' : '上下文片段'}
                />
                {turn.messages.map((message) => (
                  <MessageBubble key={message.seq} message={message} logId={log.id} />
                ))}
              </div>
            )
          })}
          {hasOlder ? (
            <UnloadedSummaryRow
              turnCount={unloadedTurns.length}
              messageCount={unloadedMessageCount}
              loading={loadingOlder}
              onLoad={onLoadOlder}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}
