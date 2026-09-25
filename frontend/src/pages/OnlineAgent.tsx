import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Menu, Plus, RefreshCw, RotateCcw, Send, Settings2, Shrink, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Button, Switch } from '../components/ui'
import { api, type OnlineAgentMessage, type OnlineAgentPart, type OnlineAgentSession } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

function textOf(message: OnlineAgentMessage) {
  return (message.parts || [])
    .filter((part) => part.type === 'text' && part.text)
    .map((part) => part.text)
    .join('\n')
}

function toolsOf(message: OnlineAgentMessage) {
  return (message.parts || []).filter((part) => part.type && !['text', 'reasoning'].includes(part.type))
}

function detailText(value: unknown) {
  if (value == null || value === '') return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function OnlineAgentPage() {
  const queryClient = useQueryClient()
  const [model, setModel] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [draft, setDraft] = useState('')
  const threadRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const state = useQuery({ queryKey: ['online-agent'], queryFn: api.onlineAgent, refetchInterval: 5000 })
  const running = Boolean(state.data?.running)
  const sessions = useQuery({
    queryKey: ['online-agent-sessions'],
    queryFn: api.onlineAgentSessions,
    enabled: running,
  })
  const messages = useQuery({
    queryKey: ['online-agent-messages', sessionId],
    queryFn: () => api.onlineAgentMessages(sessionId),
    enabled: Boolean(sessionId && running),
    refetchInterval: running ? 2500 : false,
  })

  useEffect(() => {
    if (!state.data) return
    if (!model && state.data.selected_model) setModel(state.data.selected_model)
    if (!sessionId && state.data.last_session_id) setSessionId(state.data.last_session_id)
  }, [state.data, model, sessionId])

  useEffect(() => {
    stickToBottom.current = true
    const node = threadRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [sessionId])

  useEffect(() => {
    const node = threadRef.current
    if (!node || !stickToBottom.current) return
    node.scrollTop = node.scrollHeight
  }, [messages.data?.items])

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['online-agent'] })
    await queryClient.invalidateQueries({ queryKey: ['online-agent-sessions'] })
  }

  const start = useMutation({
    mutationFn: api.startOnlineAgent,
    onSuccess: async () => {
      notifyOk('Agent 已启动')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '启动失败')),
  })
  const restart = useMutation({
    mutationFn: api.restartOnlineAgent,
    onSuccess: async () => {
      notifyOk('Agent 已重启，正在跑的任务会中断')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重启失败')),
  })
  const sync = useMutation({
    mutationFn: api.syncOnlineAgent,
    onSuccess: invalidate,
    onError: (caught) => notifyBad(errorMessage(caught, '同步失败')),
  })
  const createSession = useMutation({
    mutationFn: api.createOnlineAgentSession,
    onSuccess: async (session) => {
      setSessionId(session.id)
      setSessionsOpen(false)
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '创建会话失败')),
  })
  const send = useMutation({
    mutationFn: () => api.promptOnlineAgent(sessionId, draft, model),
    onSuccess: async () => {
      setDraft('')
      await queryClient.invalidateQueries({ queryKey: ['online-agent-messages', sessionId] })
      await queryClient.invalidateQueries({ queryKey: ['online-agent-sessions'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '发送失败')),
  })
  const compact = useMutation({
    mutationFn: () => api.compactOnlineAgent(sessionId),
    onSuccess: async () => {
      notifyOk('正在压缩当前会话')
      await queryClient.invalidateQueries({ queryKey: ['online-agent-messages', sessionId] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '压缩失败')),
  })
  const reset = useMutation({
    mutationFn: () => api.resetOnlineAgent(sessionId),
    onSuccess: async (session) => {
      setSessionId(session.id)
      notifyOk('已重置当前对话')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重置失败')),
  })

  const items = sessions.data?.items || []
  const current = items.find((item) => item.id === sessionId)
  const thread = messages.data?.items || []

  function chooseSession(id: string) {
    setSessionId(id)
    setSessionsOpen(false)
    void api.rememberOnlineAgent({ session_id: id })
  }

  function submit() {
    if (!running || !sessionId || !model || !draft.trim() || send.isPending) return
    send.mutate()
  }

  return (
    <div className="flex h-full max-h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-panel/80 lg:flex-row">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line lg:flex">
        <SessionList
          items={items}
          activeId={sessionId}
          disabled={!running || createSession.isPending}
          onCreate={() => createSession.mutate()}
          onSelect={chooseSession}
        />
      </aside>

      <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center gap-2 border-b border-line px-3 py-2">
          <Button type="button" variant="ghost" className="px-2 lg:hidden" onClick={() => setSessionsOpen(true)} aria-label="会话列表">
            <Menu size={18} />
          </Button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-paper">{current?.title || '在线 Agent'}</div>
            <div className="truncate text-xs text-mist">{state.data?.last_error || (running ? '运行中，关闭页面后任务继续' : '未启动')}</div>
          </div>
          <Button type="button" variant="ghost" className="px-2" disabled={!running || restart.isPending} onClick={() => restart.mutate()} aria-label="重启 Agent">
            <RotateCcw size={16} />
          </Button>
          <Button type="button" variant="ghost" className="px-2" disabled={!sessionId || compact.isPending} onClick={() => compact.mutate()} aria-label="压缩会话">
            <Shrink size={16} />
          </Button>
          <Button type="button" variant="ghost" className="px-2" disabled={sync.isPending} onClick={() => sync.mutate()} aria-label="同步模型">
            <RefreshCw size={16} />
          </Button>
          <Button type="button" variant="ghost" className="px-2" onClick={() => setSettingsOpen(true)} aria-label="Skills 和 MCP">
            <Settings2 size={16} />
          </Button>
        </header>

        <div
          ref={threadRef}
          className="agent-scroll min-h-0 flex-1 space-y-4 overflow-y-scroll overscroll-contain px-3 py-4 sm:px-5"
          onScroll={(event) => {
            const node = event.currentTarget
            stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80
          }}
        >
          {!running ? (
            <EmptyState
              title="启动后即可对话"
              body="任务在服务器上的同一个 Agent 里执行，关掉页面也不会取消。"
              action="启动 Agent"
              pending={start.isPending}
              onAction={() => start.mutate()}
            />
          ) : null}
          {running && !sessionId ? (
            <EmptyState
              title="从一条新会话开始"
              body="每个会话有自己的工作目录，后面拉代码不会串到别的会话。"
              action="新建会话"
              pending={createSession.isPending}
              onAction={() => createSession.mutate()}
            />
          ) : null}
          {thread.map((message, index) => (
            <MessageRow key={message.info?.id || index} message={message} />
          ))}
        </div>

        <form
          className="border-t border-line p-3"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <div className="rounded-xl border border-line bg-ink p-2">
            <textarea
              value={draft}
              disabled={!running || !sessionId}
              placeholder={running ? '描述你要 Agent 做的事' : '先启动 Agent'}
              className="max-h-40 min-h-16 w-full resize-none bg-transparent px-2 py-1 text-sm text-paper outline-none placeholder:text-mist/70"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  submit()
                }
              }}
            />
            <div className="flex items-center gap-2 px-1 pt-1">
              <select
                className="min-w-0 flex-1 truncate bg-transparent text-xs text-mist outline-none"
                value={model}
                onChange={(event) => {
                  setModel(event.target.value)
                  void api.rememberOnlineAgent({ model: event.target.value })
                }}
              >
                <option value="">选择模型</option>
                {(state.data?.models || []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              <Button type="button" variant="ghost" className="px-2 text-xs" disabled={!sessionId} onClick={() => {
                if (window.confirm('重置会删除当前会话并新建空会话。')) reset.mutate()
              }}>
                重置
              </Button>
              <Button type="submit" className="px-3" disabled={!running || !sessionId || !model || !draft.trim() || send.isPending} aria-label="发送">
                <Send size={15} />
              </Button>
            </div>
          </div>
        </form>
      </section>

      {sessionsOpen ? (
        <Drawer title="会话" onClose={() => setSessionsOpen(false)}>
          <SessionList
            items={items}
            activeId={sessionId}
            disabled={!running || createSession.isPending}
            onCreate={() => createSession.mutate()}
            onSelect={chooseSession}
          />
        </Drawer>
      ) : null}
      {settingsOpen ? (
        <Drawer title="Skills / MCP" onClose={() => setSettingsOpen(false)} side="right">
          <ToggleGroup
            title="Skills"
            items={state.data?.skills || []}
            onChange={async (id, enabled) => {
              await api.setOnlineAgentSkill(id, enabled)
              await invalidate()
            }}
          />
          <ToggleGroup
            title="MCP"
            items={state.data?.mcp || []}
            onChange={async (id, enabled) => {
              await api.setOnlineAgentMcp(id, enabled)
              await invalidate()
            }}
          />
        </Drawer>
      ) : null}
    </div>
  )
}

function SessionList({
  items,
  activeId,
  disabled,
  onCreate,
  onSelect,
}: {
  items: OnlineAgentSession[]
  activeId: string
  disabled: boolean
  onCreate: () => void
  onSelect: (id: string) => void
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between px-3 py-3">
        <div className="text-sm font-medium">会话</div>
        <Button type="button" variant="line" className="px-2" disabled={disabled} onClick={onCreate} aria-label="新建会话">
          <Plus size={15} />
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {items.map((session) => (
          <button
            key={session.id}
            type="button"
            onClick={() => onSelect(session.id)}
            className={cn(
              'block w-full truncate rounded-md px-3 py-2 text-left text-sm',
              session.id === activeId ? 'bg-signal/15 text-signal' : 'text-paper hover:bg-white/5',
            )}
          >
            {session.title || '新会话'}
          </button>
        ))}
        {!items.length ? <div className="px-3 py-6 text-xs text-mist">还没有会话</div> : null}
      </div>
    </div>
  )
}

function MessageRow({ message }: { message: OnlineAgentMessage }) {
  const mine = message.info?.role === 'user'
  const tools = toolsOf(message)
  const body = textOf(message)
  return (
    <div className={cn('flex', mine ? 'justify-end' : 'justify-start')}>
      <div className="min-w-0 max-w-[42rem]">
        {body ? (
          <div
            className={cn(
              'whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm [overflow-wrap:anywhere]',
              mine ? 'bg-signal text-ink' : 'border border-line bg-ink text-paper',
            )}
          >
            {body}
          </div>
        ) : null}
        {tools.length ? (
          <div className="mt-2 space-y-2">
            {tools.map((tool, index) => (
              <ToolStep key={`${tool.tool || tool.name || tool.type}-${index}`} part={tool} />
            ))}
          </div>
        ) : null}
        {!body && !tools.length ? <div className="text-sm text-mist">处理中</div> : null}
      </div>
    </div>
  )
}

function ToolStep({ part }: { part: OnlineAgentPart }) {
  const input = detailText(part.state?.input ?? part.input)
  const output = detailText(part.state?.output ?? part.output ?? part.text)
  const error = part.state?.error || part.error || ''
  const status = part.state?.status || (error ? 'error' : output ? 'completed' : 'running')
  return (
    <details className="overflow-hidden rounded-md border border-line bg-ink/70" open={status !== 'completed'}>
      <summary className="cursor-pointer px-3 py-2 text-xs text-paper">
        <span className="font-medium">{part.state?.title || part.tool || part.name || part.type}</span>
        <span className="ml-2 text-mist">{status}</span>
      </summary>
      <div className="space-y-2 border-t border-line px-3 py-2">
        {input ? <ToolBlock label="输入" value={input} /> : null}
        {output ? <ToolBlock label="输出" value={output} /> : null}
        {error ? <ToolBlock label="错误" value={error} danger /> : null}
      </div>
    </details>
  )
}

function ToolBlock({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div>
      <div className="text-[11px] text-mist">{label}</div>
      <pre
        className={cn(
          'mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-black/30 p-2 font-mono text-[11px] leading-5 [overflow-wrap:anywhere]',
          danger ? 'text-danger' : 'text-paper',
        )}
      >
        {value}
      </pre>
    </div>
  )
}

function EmptyState({
  title,
  body,
  action,
  pending,
  onAction,
}: {
  title: string
  body: string
  action: string
  pending: boolean
  onAction: () => void
}) {
  return (
    <div className="mx-auto flex min-h-64 max-w-md flex-col items-center justify-center text-center">
      <div className="text-lg font-medium text-paper">{title}</div>
      <p className="mt-2 text-sm text-mist">{body}</p>
      <Button type="button" className="mt-4" disabled={pending} onClick={onAction}>
        {action}
      </Button>
    </div>
  )
}

function Drawer({
  title,
  onClose,
  side = 'left',
  children,
}: {
  title: string
  onClose: () => void
  side?: 'left' | 'right'
  children: ReactNode
}) {
  return (
    <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose}>
      <div
        className={cn(
          'absolute inset-y-0 flex w-[min(100%,20rem)] flex-col bg-panel shadow-2xl',
          side === 'right' ? 'right-0' : 'left-0',
        )}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-3 py-3">
          <div className="text-sm font-medium">{title}</div>
          <Button type="button" variant="ghost" className="px-2" onClick={onClose} aria-label="关闭">
            <X size={16} />
          </Button>
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-3">{children}</div>
      </div>
    </div>
  )
}

function ToggleGroup({
  title,
  items,
  onChange,
}: {
  title: string
  items: { id: string; name: string; enabled: boolean }[]
  onChange: (id: string, enabled: boolean) => Promise<unknown>
}) {
  return (
    <div className="space-y-2">
      <div className="text-xs text-mist">{title}，默认关闭，切换后下一条消息生效</div>
      {items.map((item) => (
        <div key={item.id} className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
          <span className="min-w-0 truncate text-sm">{item.name}</span>
          <Switch
            checked={item.enabled}
            onCheckedChange={(enabled) => {
              void onChange(item.id, enabled).catch((caught) => notifyBad(errorMessage(caught, '保存失败')))
            }}
          />
        </div>
      ))}
      {!items.length ? <div className="text-xs text-mist">暂无</div> : null}
    </div>
  )
}

export default OnlineAgentPage
