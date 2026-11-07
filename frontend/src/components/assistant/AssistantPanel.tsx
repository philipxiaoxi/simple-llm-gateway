import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import {
  ArrowDown,
  ArrowUp,
  Clock3,
  History,
  Loader2,
  MessageCircle,
  Settings2,
  Sparkles,
  Square,
  SquarePen,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { cn } from '../../lib/utils'
import { useCan } from '../../lib/session'
import { notifyBad } from '../../lib/toast'
import { AssistantHistoryDialog } from './AssistantHistoryDialog'
import { AssistantSettingsDialog } from './AssistantSettingsDialog'
import { MessageBubble } from './MessageBubble'
import { useAssistant } from './AssistantProvider'

const SUGGESTIONS = [
  { label: '打开 MCP 广场', prompt: '帮我打开 MCP 广场' },
  { label: '查看最近的日志', prompt: '帮我看看最近的请求日志' },
  { label: '下载抖音视频', prompt: '帮我下载这个抖音视频：' },
  { label: '检索知识库', prompt: '在知识库里检索：' },
]

export function AssistantPanel() {
  const {
    open,
    setOpen,
    messages,
    streaming,
    stage,
    error,
    conversationId,
    title,
    settings,
    settingsLoading,
    conversations,
    conversationsLoading,
    hasPendingApproval,
    send,
    stop,
    newConversation,
    loadConversation,
    deleteConversation,
    decideApproval,
    refreshSettings,
  } = useAssistant()
  const canManage = useCan('assistant:manage')
  const reduce = useReducedMotion()
  const [draft, setDraft] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [nearBottom, setNearBottom] = useState(true)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const nearBottomRef = useRef(true)
  const userScrollRef = useRef(false)
  const lastScrollTopRef = useRef(0)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const configured = Boolean(settings?.configured)
  const hasMessages = messages.length > 0

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => inputRef.current?.focus(), 120)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    if (!open) return
    const handler = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (settingsOpen || historyOpen) return
      setOpen(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, setOpen, settingsOpen, historyOpen])

  useEffect(() => {
    const node = scrollRef.current
    if (!node || !nearBottomRef.current) return
    node.scrollTop = node.scrollHeight
  }, [messages, streaming, stage])

  useEffect(() => {
    if (streaming || !nearBottomRef.current) return
    const timer = window.setTimeout(() => {
      const node = scrollRef.current
      if (node && nearBottomRef.current) node.scrollTop = node.scrollHeight
    }, 80)
    return () => window.clearTimeout(timer)
  }, [streaming, messages])

  const scrollToBottom = (smooth = false) => {
    const node = scrollRef.current
    nearBottomRef.current = true
    userScrollRef.current = false
    setNearBottom(true)
    if (!node) return
    node.scrollTo({ top: node.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }

  const onScroll = () => {
    const node = scrollRef.current
    if (!node) return
    const movedUp = node.scrollTop < lastScrollTopRef.current - 4
    lastScrollTopRef.current = node.scrollTop
    if (!userScrollRef.current && !movedUp) return
    const next = node.scrollHeight - node.scrollTop - node.clientHeight < 80
    nearBottomRef.current = next
    setNearBottom(next)
  }

  useEffect(() => {
    const node = scrollRef.current
    const content = contentRef.current
    if (!node || !content || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (nearBottomRef.current) node.scrollTop = node.scrollHeight
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [hasMessages])

  const submit = () => {
    if (!draft.trim() || streaming || !configured || hasPendingApproval) return
    const value = draft
    setDraft('')
    scrollToBottom()
    void send(value)
  }

  const applySuggestion = (prompt: string) => {
    setDraft(prompt)
    inputRef.current?.focus()
  }

  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.button
            type="button"
            aria-label="关闭助手"
            className="fixed inset-0 z-30 bg-black/45 backdrop-blur-[2px] lg:hidden"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          />
          <motion.aside
            role="dialog"
            aria-label="AI 助手"
            className={cn(
              'fixed inset-0 z-40 flex h-[100dvh] flex-col overflow-hidden border-line bg-panel text-paper',
              'lg:inset-auto lg:bottom-6 lg:right-6 lg:h-[min(760px,calc(100dvh-3rem))] lg:w-[420px] lg:rounded-2xl lg:border lg:shadow-floating',
            )}
            initial={reduce ? false : { opacity: 0, y: 18, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: 14, scale: 0.985 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            <header className="relative shrink-0 overflow-hidden border-b border-line-strong bg-panel-2 px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))] lg:pt-3">
              <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(200,245,66,0.14),transparent_58%),radial-gradient(circle_at_90%_10%,rgba(110,200,255,0.12),transparent_48%)]" />
              <div className="relative flex items-center gap-3">
                <span className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-signal/30 bg-gradient-to-br from-signal/25 via-panel-2 to-info/15 text-signal shadow-[inset_0_1px_0_rgba(255,255,255,0.12)]">
                  <Sparkles size={18} />
                  <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-panel bg-signal" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-sm font-semibold tracking-wide">站点助手</h2>
                    {settings?.model ? (
                      <span className="hidden max-w-[130px] truncate rounded-full border border-line bg-ink/50 px-2 py-0.5 font-mono text-[10px] text-mist-soft sm:inline">
                        {settings.model}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-mist-soft">
                    {streaming ? stage || '正在处理' : title && title !== '新对话' ? title : '调用站内接口，连续对话'}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    type="button"
                    aria-label="对话记录"
                    title="对话记录"
                    className="flex h-8 w-8 items-center justify-center rounded-md text-mist transition-colors hover:bg-elevate hover:text-paper"
                    onClick={() => setHistoryOpen(true)}
                  >
                    <History size={16} />
                  </button>
                  {canManage ? (
                    <button
                      type="button"
                      aria-label="助手设置"
                      title="助手设置"
                      className="flex h-8 w-8 items-center justify-center rounded-md text-mist transition-colors hover:bg-elevate hover:text-paper"
                      onClick={() => setSettingsOpen(true)}
                    >
                      <Settings2 size={16} />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    aria-label="关闭助手"
                    title="关闭助手"
                    className="flex h-8 w-8 items-center justify-center rounded-md text-mist transition-colors hover:bg-elevate hover:text-paper"
                    onClick={() => setOpen(false)}
                  >
                    <X size={17} />
                  </button>
                </div>
              </div>
            </header>

            {!configured && !settingsLoading ? (
              <div className="flex shrink-0 items-center gap-2 border-b border-warn/20 bg-warn/10 px-4 py-2 text-[11px] leading-5 text-warn">
                <MessageCircle size={13} className="shrink-0" />
                <span className="min-w-0 flex-1">
                  {canManage ? '还没有配置对话模型，配好就能开始使用。' : '助手还没有配置对话模型，请联系管理员。'}
                </span>
                {canManage ? (
                  <button
                    type="button"
                    className="shrink-0 rounded-md border border-warn/40 px-2 py-0.5 font-medium text-warn transition-colors hover:bg-warn/10"
                    onClick={() => setSettingsOpen(true)}
                  >
                    去配置
                  </button>
                ) : null}
              </div>
            ) : null}

            {hasPendingApproval ? (
              <div className="flex shrink-0 items-center gap-2 border-b border-warn/20 bg-warn/10 px-4 py-1.5 text-[11px] leading-5 text-warn">
                <Clock3 size={13} className="shrink-0" />
                有操作等待你确认，处理后可继续对话。
              </div>
            ) : null}

            {error && configured ? (
              <div className="shrink-0 border-b border-danger/20 bg-danger/10 px-4 py-1.5 text-[11px] leading-5 text-danger">
                {error}
              </div>
            ) : null}

            <div className="relative min-h-0 flex-1">
              <div
                ref={scrollRef}
                onScroll={onScroll}
                onWheel={() => {
                  userScrollRef.current = true
                }}
                onTouchMove={() => {
                  userScrollRef.current = true
                }}
                onPointerDown={(event) => {
                  if (event.target === event.currentTarget) userScrollRef.current = true
                }}
                onKeyDown={(event) => {
                  if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
                    userScrollRef.current = true
                  }
                }}
                className="assistant-scroll h-full overflow-y-auto overflow-x-hidden overscroll-none bg-panel bg-[radial-gradient(circle_at_50%_-10%,rgba(200,245,66,0.045),transparent_38%)] px-3 py-4 shadow-[inset_0_10px_18px_-18px_rgba(0,0,0,0.7)]"
              >
              {messages.length ? (
                <div ref={contentRef} className="mx-auto grid w-full max-w-2xl content-start gap-4">
                  {messages.map((message) => (
                    <MessageBubble
                      key={message.id}
                      message={message}
                      onDecideApproval={(approvalId, decision) => void decideApproval(approvalId, decision)}
                    />
                  ))}
                  {streaming && !messages.at(-1)?.pending && stage ? (
                    <div className="flex items-center gap-2 pl-9 text-[11px] text-mist-soft">
                      <Loader2 size={12} className="animate-spin text-signal" />
                      {stage}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="mx-auto flex h-full w-full max-w-md flex-col items-center justify-center px-4 pb-8 text-center">
                  <div className="relative mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-signal/25 bg-gradient-to-br from-signal/20 via-panel-2 to-info/10 text-signal shadow-glow-soft">
                    <Sparkles size={28} />
                    <span className="absolute inset-0 rounded-2xl bg-signal/10 blur-xl" aria-hidden="true" />
                  </div>
                  <h3 className="text-base font-semibold text-paper">今天想做什么？</h3>
                  <p className="mt-1.5 max-w-xs text-xs leading-5 text-mist">
                    我可以查询站内数据、执行操作、打开页面。写操作会先暂停，等你审批。
                  </p>
                  <div className="mt-5 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
                    {SUGGESTIONS.map((item) => (
                      <button
                        key={item.label}
                        type="button"
                        disabled={!configured}
                        onClick={() => applySuggestion(item.prompt)}
                        className="group flex min-h-10 items-center justify-between gap-2 rounded-xl border border-line bg-panel-2/60 px-3 py-2 text-left text-xs text-paper transition-all hover:border-signal/35 hover:bg-signal/5 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <span className="truncate">{item.label}</span>
                        <ArrowUp size={13} className="shrink-0 text-mist transition-transform group-hover:-translate-y-0.5 group-hover:text-signal" />
                      </button>
                    ))}
                  </div>
                  <div className="mt-4 flex items-center gap-1.5 text-[10px] text-mist-soft">
                    <span className="h-1 w-1 rounded-full bg-signal" />
                    接口按当前账号权限调用，写操作需要审批
                  </div>
                </div>
              )}
              </div>

              {!nearBottom && messages.length ? (
                <button
                  type="button"
                  className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-panel/95 px-3 py-1.5 text-[11px] text-mist shadow-menu backdrop-blur transition-colors hover:text-paper"
                  onClick={() => scrollToBottom(true)}
                >
                  <ArrowDown size={12} />
                  回到底部
                </button>
              ) : null}
            </div>

            <div className="shrink-0 border-t border-line bg-panel px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-3">
              <div className="mx-auto w-full max-w-2xl">
                <div className="flex items-end gap-2 rounded-2xl border border-line-strong bg-control p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] transition-[border-color,box-shadow] focus-within:border-signal/60 focus-within:ring-2 focus-within:ring-signal/15">
                  <button
                    type="button"
                    aria-label="新建对话"
                    title="新建对话"
                    disabled={streaming}
                    className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-mist transition-colors hover:bg-elevate hover:text-paper disabled:opacity-35"
                    onClick={newConversation}
                  >
                    <SquarePen size={16} />
                  </button>
                  <textarea
                    ref={inputRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault()
                        submit()
                      }
                    }}
                    rows={1}
                    placeholder={
                      !configured
                        ? '请先配置对话模型'
                        : hasPendingApproval
                          ? '请先处理待审批操作'
                          : '描述你想完成的任务…'
                    }
                    disabled={!configured || hasPendingApproval}
                    className="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm leading-5 text-paper outline-none placeholder:text-mist-soft disabled:cursor-not-allowed"
                  />
                  {streaming ? (
                    <button
                      type="button"
                      aria-label="停止生成"
                      title="停止生成"
                      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-line bg-panel text-paper transition-colors hover:border-danger/40 hover:text-danger"
                      onClick={stop}
                    >
                      <Square size={14} fill="currentColor" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      aria-label="发送"
                      title="发送"
                      disabled={!draft.trim() || !configured || hasPendingApproval}
                      className={cn(
                        'flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-all',
                        draft.trim() && configured && !hasPendingApproval
                          ? 'bg-signal text-on-signal shadow-[0_6px_20px_-8px_rgba(200,245,66,0.8)] hover:brightness-110 active:scale-95'
                          : 'bg-elevate text-mist-soft',
                      )}
                      onClick={submit}
                    >
                      <ArrowUp size={17} />
                    </button>
                  )}
                </div>
                <div className="mt-1.5 flex items-center justify-between gap-2 px-1 text-[10px] text-mist-soft">
                  <span>Enter 发送 · Shift+Enter 换行</span>
                  <span className="truncate">
                    {hasPendingApproval ? '等待审批' : streaming ? stage || '正在处理' : settings?.model || '未配置模型'}
                  </span>
                </div>
              </div>
            </div>
          </motion.aside>

          {settingsOpen && settings ? (
            <AssistantSettingsDialog
              settings={settings}
              onClose={() => setSettingsOpen(false)}
              onSaved={async () => {
                await refreshSettings()
              }}
            />
          ) : null}
          {historyOpen ? (
            <AssistantHistoryDialog
              conversations={conversations}
              currentId={conversationId}
              loading={conversationsLoading}
              onSelect={(id) => void loadConversation(id)}
              onDelete={async (id) => {
                try {
                  await deleteConversation(id)
                } catch (caught) {
                  notifyBad(caught instanceof Error ? caught.message : '删除失败')
                }
              }}
              onNew={newConversation}
              onClose={() => setHistoryOpen(false)}
            />
          ) : null}
        </>
      ) : null}
    </AnimatePresence>
  )
}
