import { MessageSquare, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { AssistantConversationSummary } from '../../lib/api'
import { cn, formatTime } from '../../lib/utils'
import { Button, Dialog } from '../ui'

export function AssistantHistoryDialog({
  conversations,
  currentId,
  loading,
  onSelect,
  onDelete,
  onNew,
  onClose,
}: {
  conversations: AssistantConversationSummary[]
  currentId: string | null
  loading: boolean
  onSelect: (id: string) => void
  onDelete: (id: string) => Promise<void>
  onNew: () => void
  onClose: () => void
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [pendingId, setPendingId] = useState<string | null>(null)

  return (
    <Dialog
      title="对话记录"
      onClose={onClose}
      className="max-w-xl"
      footer={
        <Button
          type="button"
          variant="line"
          className="w-full justify-center gap-2"
          onClick={() => {
            onNew()
            onClose()
          }}
        >
          <Plus size={15} />
          新建对话
        </Button>
      }
    >
      {loading ? (
        <div className="grid gap-2">
          {[0, 1, 2].map((index) => (
            <div key={index} className="h-14 animate-pulse rounded-xl border border-line bg-panel-2/40" />
          ))}
        </div>
      ) : conversations.length ? (
        <div className="grid gap-1.5">
          {conversations.map((conversation) => {
            const active = conversation.id === currentId
            return (
              <div
                key={conversation.id}
                className={cn(
                  'group flex min-w-0 items-center gap-2 rounded-xl border px-3 py-2.5 transition-colors',
                  active
                    ? 'border-signal/35 bg-signal/10'
                    : 'border-line bg-panel-2/50 hover:border-mist/30 hover:bg-panel-2',
                )}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                  onClick={() => {
                    onSelect(conversation.id)
                    onClose()
                  }}
                >
                  <span
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
                      active ? 'border-signal/30 bg-signal/10 text-signal' : 'border-line bg-panel text-mist',
                    )}
                  >
                    <MessageSquare size={14} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-paper">{conversation.title}</span>
                    <span className="mt-0.5 block truncate text-[11px] text-mist-soft">
                      {conversation.message_count} 条消息 · {formatTime(conversation.updated_at)}
                    </span>
                  </span>
                </button>
                {confirmId === conversation.id ? (
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      disabled={pendingId === conversation.id}
                      onClick={async () => {
                        setPendingId(conversation.id)
                        try {
                          await onDelete(conversation.id)
                          setConfirmId(null)
                        } finally {
                          setPendingId(null)
                        }
                      }}
                    >
                      确认
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmId(null)}>
                      取消
                    </Button>
                  </div>
                ) : (
                  <button
                    type="button"
                    aria-label="删除对话"
                    title="删除对话"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-mist-soft transition-colors hover:bg-danger/10 hover:text-danger"
                    onClick={() => setConfirmId(conversation.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-panel-2 text-mist">
            <MessageSquare size={20} />
          </span>
          <div className="text-sm text-paper">还没有对话</div>
          <div className="text-xs text-mist">发一条消息，助手会把它保存成连续对话。</div>
        </div>
      )}
    </Dialog>
  )
}
