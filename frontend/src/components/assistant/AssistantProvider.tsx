import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import {
  api,
  type AssistantApproval,
  type AssistantConversation,
  type AssistantConversationSummary,
  type AssistantMessage,
  type AssistantSettings,
} from '../../lib/api'
import { streamApprovalDecision, streamAssistantMessage, toolLabel } from '../../lib/assistant'
import { notifyBad, notifyOk } from '../../lib/toast'

export type UiToolCall = {
  id: string
  name: string
  arguments: Record<string, unknown>
  result: unknown
  ok: boolean
  summary: string
  error: string
  status: 'running' | 'done' | 'error' | 'waiting'
  latencyMs?: number
  approval?: AssistantApproval
}

export type UiSegment =
  | { type: 'text'; content: string }
  | { type: 'reasoning'; content: string }
  | { type: 'tool'; id: string }

export type UiMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  reasoning: string
  segments: UiSegment[]
  toolCalls: UiToolCall[]
  pending: boolean
  error?: string
  createdAt?: string | null
}

type AssistantContextValue = {
  open: boolean
  setOpen: (open: boolean) => void
  messages: UiMessage[]
  streaming: boolean
  stage: string
  error: string | null
  conversationId: string | null
  lastConversation: AssistantConversationSummary | null
  title: string
  settings?: AssistantSettings
  settingsLoading: boolean
  conversations: AssistantConversationSummary[]
  conversationsLoading: boolean
  hasPendingApproval: boolean
  send: (text: string) => Promise<void>
  stop: () => void
  newConversation: () => void
  loadConversation: (id: string) => Promise<void>
  deleteConversation: (id: string) => Promise<void>
  decideApproval: (approvalId: string, decision: 'approve' | 'reject') => Promise<void>
  refreshSettings: () => Promise<void>
  refreshConversations: () => Promise<void>
}

const AssistantContext = createContext<AssistantContextValue | null>(null)

function serverMessages(data: AssistantConversation): UiMessage[] {
  return data.messages.map((message: AssistantMessage) => {
    const segments: UiSegment[] = []
    if (message.reasoning.trim()) segments.push({ type: 'reasoning', content: message.reasoning })
    if (message.content.trim()) segments.push({ type: 'text', content: message.content })
    for (const call of message.tool_calls) segments.push({ type: 'tool', id: call.id })
    return {
      id: `srv-${message.id}`,
      role: message.role,
      content: message.content,
      reasoning: message.reasoning || '',
      segments,
      toolCalls: message.tool_calls.map((call) => ({
        ...call,
        latencyMs: call.latency_ms ?? 0,
        status: call.approval?.status === 'pending' ? 'waiting' : call.ok ? 'done' : 'error',
      })),
      pending: false,
      error: message.error,
      createdAt: message.created_at,
    }
  })
}

export function AssistantProvider({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [title, setTitle] = useState('新对话')
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [streaming, setStreaming] = useState(false)
  const [stage, setStage] = useState('')
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['assistant', 'settings'],
    queryFn: api.assistantSettings,
    enabled: open,
    staleTime: 30_000,
    retry: false,
  })
  const conversationsQuery = useQuery({
    queryKey: ['assistant', 'conversations'],
    queryFn: api.assistantConversations,
    enabled: open,
    staleTime: 10_000,
    retry: false,
  })

  const refreshSettings = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['assistant', 'settings'] })
  }, [queryClient])

  const refreshConversations = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['assistant', 'conversations'] })
  }, [queryClient])

  const updateAssistant = useCallback((assistantId: string, updater: (message: UiMessage) => UiMessage) => {
    setMessages((current) =>
      current.map((message) => (message.id === assistantId ? updater(message) : message)),
    )
  }, [])

  const updateToolCall = useCallback((callId: string, updater: (call: UiToolCall) => UiToolCall) => {
    setMessages((current) =>
      current.map((message) => ({
        ...message,
        toolCalls: message.toolCalls.map((call) => (call.id === callId ? updater(call) : call)),
      })),
    )
  }, [])

  const applyEvent = useCallback(
    (assistantId: string, event: string, data: Record<string, unknown>) => {
      if (event === 'meta') {
        const nextTitle = typeof data.title === 'string' ? data.title : ''
        if (nextTitle) setTitle(nextTitle)
        if (typeof data.conversation_id === 'string') {
          setConversationId(data.conversation_id)
        }
        return
      }
      if (event === 'status') {
        const stageName = typeof data.stage === 'string' ? data.stage : ''
        if (stageName === 'thinking') setStage('正在思考')
        else if (stageName === 'writing') setStage('正在回复')
        else if (stageName === 'tool') {
          const name = typeof data.name === 'string' ? data.name : '工具'
          setStage(`正在执行 ${toolLabel(name)}`)
        }
        return
      }
      if (event === 'reasoning') {
        const delta = typeof data.delta === 'string' ? data.delta : ''
        if (!delta) return
        updateAssistant(assistantId, (message) => {
          const segments = [...message.segments]
          const last = segments[segments.length - 1]
          if (last && last.type === 'reasoning') {
            segments[segments.length - 1] = { type: 'reasoning', content: last.content + delta }
          } else {
            segments.push({ type: 'reasoning', content: delta })
          }
          return {
            ...message,
            reasoning: message.reasoning + delta,
            segments,
            pending: message.content ? false : message.pending,
          }
        })
        return
      }
      if (event === 'text') {
        const delta = typeof data.delta === 'string' ? data.delta : ''
        if (!delta) return
        updateAssistant(assistantId, (message) => {
          const segments = [...message.segments]
          const last = segments[segments.length - 1]
          if (last && last.type === 'text') {
            segments[segments.length - 1] = { type: 'text', content: last.content + delta }
          } else {
            segments.push({ type: 'text', content: delta })
          }
          return { ...message, content: message.content + delta, segments, pending: false }
        })
        return
      }
      if (event === 'tool_call') {
        const callId = typeof data.id === 'string' ? data.id : `tool-${Date.now()}`
        const name = typeof data.name === 'string' ? data.name : ''
        const args =
          data.arguments && typeof data.arguments === 'object'
            ? (data.arguments as Record<string, unknown>)
            : {}
        updateAssistant(assistantId, (message) => ({
          ...message,
          pending: false,
          segments: [...message.segments, { type: 'tool', id: callId }],
          toolCalls: [
            ...message.toolCalls,
            {
              id: callId,
              name,
              arguments: args,
              result: null,
              ok: true,
              summary: '',
              error: '',
              status: 'running',
            },
          ],
        }))
        return
      }
      if (event === 'tool_result') {
        const callId = typeof data.id === 'string' ? data.id : ''
        const ok = Boolean(data.ok)
        const approvalStatus =
          data.kind === 'approval'
            ? ok
              ? 'executed'
              : data.error === '用户拒绝执行'
                ? 'rejected'
                : 'failed'
            : undefined
        updateToolCall(callId, (call) => ({
          ...call,
          status: ok ? 'done' : 'error',
          ok,
          result: data.result,
          summary: typeof data.summary === 'string' ? data.summary : '',
          error: typeof data.error === 'string' ? data.error : '',
          latencyMs: typeof data.latency_ms === 'number' ? data.latency_ms : 0,
          approval: call.approval
            ? { ...call.approval, status: approvalStatus || call.approval.status }
            : call.approval,
        }))
        return
      }
      if (event === 'approval_required') {
        const callId = typeof data.tool_call_id === 'string' ? data.tool_call_id : ''
        const approval = data.approval && typeof data.approval === 'object'
          ? (data.approval as AssistantApproval)
          : null
        if (!approval) return
        setStage('等待审批')
        updateToolCall(callId, (call) => ({
          ...call,
          status: 'waiting',
          summary: typeof approval.summary === 'string' ? approval.summary : call.summary,
          approval,
        }))
        return
      }
      if (event === 'navigate') {
        const path = typeof data.path === 'string' ? data.path : ''
        if (!path.startsWith('/')) return
        navigate(path)
        setOpen(false)
        const title = typeof data.title === 'string' ? data.title : path
        notifyOk(`已打开${title}`)
        return
      }
      if (event === 'error') {
        const message = typeof data.message === 'string' ? data.message : '助手执行失败'
        setError(message)
        updateAssistant(assistantId, (current) => ({
          ...current,
          pending: false,
          error: message,
        }))
        return
      }
      if (event === 'done') {
        const nextTitle = typeof data.title === 'string' ? data.title : ''
        if (nextTitle) setTitle(nextTitle)
        updateAssistant(assistantId, (message) => ({ ...message, pending: false }))
        if (data.awaiting_approval) setStage('等待审批')
      }
    },
    [navigate, updateAssistant, updateToolCall],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    setStage('')
    setMessages((current) =>
      current.map((message) =>
        message.pending
          ? {
              ...message,
              pending: false,
              error: message.content || message.reasoning ? undefined : '已停止生成',
            }
          : message,
      ),
    )
  }, [])

  const newConversation = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setStreaming(false)
    setStage('')
    setError(null)
    setConversationId(null)
    setTitle('新对话')
    setMessages([])
  }, [])

  const loadConversation = useCallback(
    async (id: string) => {
      abortRef.current?.abort()
      abortRef.current = null
      setStreaming(false)
      setStage('')
      setError(null)
      try {
        const data = await api.assistantConversation(id)
        setConversationId(data.id)
        setTitle(data.title)
        setMessages(serverMessages(data))
        setOpen(true)
      } catch (caught) {
        notifyBad(caught instanceof Error ? caught.message : '会话加载失败')
      }
    },
    [],
  )

  const deleteConversation = useCallback(
    async (id: string) => {
      await api.deleteAssistantConversation(id)
      await refreshConversations()
      if (conversationId === id) newConversation()
    },
    [conversationId, newConversation, refreshConversations],
  )

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text || streaming) return
      setError(null)
      let activeId = conversationId
      if (!activeId) {
        try {
          const created = await api.createAssistantConversation()
          activeId = created.id
          setConversationId(activeId)
          setTitle(created.title)
          await refreshConversations()
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : '创建会话失败')
          return
        }
      }

      const stamp = Date.now()
      const assistantId = `local-assistant-${stamp}`
      setMessages((current) => [
        ...current,
        {
          id: `local-user-${stamp}`,
          role: 'user',
          content: text,
          reasoning: '',
          segments: [{ type: 'text', content: text }],
          toolCalls: [],
          pending: false,
          createdAt: new Date().toISOString(),
        },
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          reasoning: '',
          segments: [],
          toolCalls: [],
          pending: true,
          createdAt: new Date().toISOString(),
        },
      ])
      setStreaming(true)
      setStage('正在思考')
      const controller = new AbortController()
      abortRef.current = controller
      let completed = false

      try {
        await streamAssistantMessage(
          activeId,
          {
            content: text,
            page: {
              path: location.pathname,
              title: document.title,
              selection: window.getSelection()?.toString().slice(0, 500) || '',
            },
          },
          {
            signal: controller.signal,
            onEvent: (event, data) => {
              if (event === 'done') completed = true
              if (event === 'error') completed = true
              applyEvent(assistantId, event, data)
            },
          },
        )
        if (!completed) {
          updateAssistant(assistantId, (message) => ({
            ...message,
            pending: false,
            error: message.content ? undefined : '连接已断开，请重试',
          }))
        }
        await refreshConversations()
      } catch (caught) {
        if (controller.signal.aborted) {
          updateAssistant(assistantId, (message) => ({
            ...message,
            pending: false,
            error: message.content ? undefined : '已停止生成',
          }))
        } else {
          const message = caught instanceof Error ? caught.message : '请求失败'
          setError(message)
          updateAssistant(assistantId, (current) => ({
            ...current,
            pending: false,
            error: message,
          }))
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
        setStage('')
      }
    },
    [applyEvent, conversationId, location.pathname, refreshConversations, streaming, updateAssistant],
  )

  const decideApproval = useCallback(
    async (approvalId: string, decision: 'approve' | 'reject') => {
      if (!conversationId || streaming) return
      const call = messages.flatMap((message) => message.toolCalls).find((item) => item.approval?.id === approvalId)
      if (!call) return
      setError(null)
      setStreaming(true)
      setStage(decision === 'approve' ? '正在执行审批' : '正在取消操作')
      updateToolCall(call.id, (item) => ({
        ...item,
        status: 'running',
        approval: item.approval ? { ...item.approval, status: decision === 'approve' ? 'approved' : 'rejected' } : item.approval,
      }))
      const stamp = Date.now()
      const assistantId = `local-approval-${stamp}`
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          reasoning: '',
          segments: [],
          toolCalls: [],
          pending: true,
          createdAt: new Date().toISOString(),
        },
      ])
      const controller = new AbortController()
      abortRef.current = controller
      let completed = false
      try {
        await streamApprovalDecision(
          approvalId,
          {
            decision,
            page: {
              path: location.pathname,
              title: document.title,
            },
          },
          {
            signal: controller.signal,
            onEvent: (event, data) => {
              if (event === 'done') completed = true
              if (event === 'error') completed = true
              applyEvent(assistantId, event, data)
            },
          },
        )
        if (!completed) {
          updateAssistant(assistantId, (message) => ({
            ...message,
            pending: false,
            error: message.content ? undefined : '连接已断开，请重试',
          }))
        }
        await refreshConversations()
      } catch (caught) {
        const message = caught instanceof Error ? caught.message : '审批失败'
        setError(message)
        updateAssistant(assistantId, (current) => ({ ...current, pending: false, error: message }))
        updateToolCall(call.id, (item) => ({
          ...item,
          status: 'error',
          error: message,
          approval: item.approval ? { ...item.approval, status: 'failed' } : item.approval,
        }))
      } finally {
        abortRef.current = null
        setStreaming(false)
        setStage('')
      }
    },
    [applyEvent, conversationId, location.pathname, messages, refreshConversations, streaming, updateAssistant, updateToolCall],
  )

  const lastConversation = useMemo(
    () => conversationsQuery.data?.items.find((item) => item.id !== conversationId) ?? null,
    [conversationsQuery.data?.items, conversationId],
  )

  const hasPendingApproval = useMemo(
    () => messages.some((message) => message.toolCalls.some((call) => call.approval?.status === 'pending')),
    [messages],
  )

  const value = useMemo<AssistantContextValue>(
    () => ({
      open,
      setOpen,
      messages,
      streaming,
      stage,
      error,
      conversationId,
      lastConversation,
      title,
      settings: settingsQuery.data,
      settingsLoading: settingsQuery.isPending,
      conversations: conversationsQuery.data?.items ?? [],
      conversationsLoading: conversationsQuery.isPending,
      hasPendingApproval,
      send,
      stop,
      newConversation,
      loadConversation,
      deleteConversation,
      decideApproval,
      refreshSettings,
      refreshConversations,
    }),
    [
      open,
      messages,
      streaming,
      stage,
      error,
      conversationId,
      lastConversation,
      title,
      settingsQuery.data,
      settingsQuery.isPending,
      conversationsQuery.data?.items,
      conversationsQuery.isPending,
      hasPendingApproval,
      send,
      stop,
      newConversation,
      loadConversation,
      deleteConversation,
      decideApproval,
      refreshSettings,
      refreshConversations,
    ],
  )

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>
}

export function useAssistant() {
  const value = useContext(AssistantContext)
  if (!value) throw new Error('useAssistant 必须在 AssistantProvider 内使用')
  return value
}
