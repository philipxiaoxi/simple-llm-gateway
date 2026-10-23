import { useMemo } from 'react'
import type { LogItem, LogMessageItem, LogTurn } from '../lib/api'
import {
  buildExecutionSteps,
  buildTurns,
  formatLogTime,
  protocolEntry,
  protocolLabel,
  statusMeta,
  summarizeTurn,
} from '../lib/logView'
import { cn } from '../lib/utils'
import { Badge, Button } from './ui'
import { MetaItem } from './LogsShared'

const toneDot: Record<string, string> = {
  ok: 'bg-signal',
  bad: 'bg-danger',
  warn: 'bg-warn',
  info: 'bg-info',
  mist: 'bg-mist',
}

const toneText: Record<string, string> = {
  ok: 'text-signal',
  bad: 'text-danger',
  warn: 'text-warn',
  info: 'text-info',
  mist: 'text-mist',
}

export function LogTrajectory({
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
  const steps = buildExecutionSteps(log)
  const loadedTurns = useMemo(() => buildTurns(messages, log.reasoning_summary ?? {}), [messages, log.reasoning_summary])
  const loadedByAnchor = useMemo(
    () => new Map(loadedTurns.map((turn) => [turn.messages[0]?.seq, summarizeTurn(turn)])),
    [loadedTurns],
  )
  // 最新在前：轮次倒序展示，加载更早的按钮在列表底部
  const displayTurns = useMemo(() => [...turnsOutline].reverse(), [turnsOutline])
  const meta = statusMeta(log.status)
  const tokens = log.total_tokens ?? (log.prompt_tokens ?? 0) + (log.completion_tokens ?? 0)
  const chain = log.model_chain

  return (
    <div className="min-w-0 space-y-5">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm lg:grid-cols-4">
        <MetaItem label="协议">{protocolLabel(log.protocol)}</MetaItem>
        <MetaItem label="传输">{log.stream ? '流式' : '非流式'}</MetaItem>
        <MetaItem label="模型">{chain?.public_id || chain?.upstream || log.model || '—'}</MetaItem>
        <MetaItem label="状态">
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </MetaItem>
        <MetaItem label="入口">
          <span className="font-mono text-xs">{protocolEntry(log.protocol)}</span>
        </MetaItem>
        <MetaItem label="开始">{formatLogTime(log.created_at)}</MetaItem>
        <MetaItem label="最近活动">{formatLogTime(log.updated_at || log.created_at)}</MetaItem>
        <MetaItem label="调用次数">{log.turn_count ?? 1}</MetaItem>
        <MetaItem label="最近一次耗时">{(log.last_latency_ms ?? log.latency_ms)}ms</MetaItem>
        <MetaItem label="累计耗时">{log.latency_ms}ms</MetaItem>
      </div>

      <div className="rounded-xl border border-line bg-panel/60 p-4">
        <div className="mb-3 text-xs uppercase tracking-[0.16em] text-mist">执行链路</div>
        <ol className="space-y-0">
          {steps.map((step, index) => (
            <li key={step.key} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className={cn('mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full', toneDot[step.tone])} />
                {index < steps.length - 1 ? <span className="w-px flex-1 bg-line" /> : null}
              </div>
              <div className="min-w-0 flex-1 pb-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn('text-sm font-medium', toneText[step.tone] ?? 'text-paper')}>{step.label}</span>
                  <span className="min-w-0 truncate text-xs text-mist">{step.detail}</span>
                </div>
                {step.hint ? <div className="mt-0.5 truncate font-mono text-[11px] text-mist-strong">{step.hint}</div> : null}
              </div>
            </li>
          ))}
        </ol>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">轮次明细</div>
          <div className="text-xs text-mist">
            {turnsOutline.length} 轮 · 最新在前 · 累计 {tokens} Token
          </div>
        </div>
        {turnsOutline.length === 0 ? (
          <div className="rounded-xl border border-line bg-panel/60 px-4 py-8 text-center text-sm text-mist">
            没有可预览的轮次明细。
          </div>
        ) : (
          <ol className="space-y-2">
            {displayTurns.map((turn) => {
              const summary = loadedByAnchor.get(turn.anchor_seq)
              return (
                <li key={`turn-${turn.anchor_seq}`}>
                  <button
                    type="button"
                    onClick={() => onPreviewTurn(turn.anchor_seq, turn.to_seq, turn.index)}
                    className="flex w-full items-start gap-3 rounded-xl border border-line bg-panel/60 p-3 text-left transition hover:border-mist/40 hover:bg-white/[0.03]"
                  >
                    <Badge tone="mist">{turn.index != null ? `第 ${turn.index} 轮` : '上下文'}</Badge>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm" title={summary?.preview}>
                        {summary ? summary.preview : '（消息尚未加载，点击预览查看）'}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-mist">
                        <span>{turn.message_count} 条消息</span>
                        {summary ? <span>{summary.textLength} 字</span> : null}
                        {summary?.reasoningCount ? <span>思考 {summary.reasoningCount}</span> : null}
                        {summary?.toolCount ? <span>工具调用 {summary.toolCount}</span> : null}
                      </div>
                    </div>
                    <span className="shrink-0 text-xs text-signal">预览</span>
                  </button>
                </li>
              )
            })}
          </ol>
        )}
        {hasOlder ? (
          <div className="pt-2 text-center">
            <Button variant="line" disabled={loadingOlder} onClick={onLoadOlder}>
              {loadingOlder ? '加载中…' : '加载更早的轮次'}
            </Button>
          </div>
        ) : null}
      </div>

      {log.error_message ? (
        <div className="rounded-xl border border-danger/40 bg-danger/[0.06] p-4 text-sm text-danger">
          <div className="mb-1 text-xs uppercase tracking-[0.16em] text-danger/80">错误信息</div>
          <div className="break-words [overflow-wrap:anywhere]">{log.error_message}</div>
        </div>
      ) : null}
    </div>
  )
}
