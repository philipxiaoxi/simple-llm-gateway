import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Check,
  Copy,
  Loader2,
  Mic,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
  Settings,
  Smartphone,
  Sparkles,
  Wand2,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { api } from '../lib/api'
import type { VoicePolishMode, VoiceRoomUpdateInput } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { LOG_PAGE_SIZE, cn, errorMessage, formatTime } from '../lib/utils'
import { Pagination } from '../components/Pagination'

const ASR_MODELS = [
  'qwen-audio-3.0-asr-flash-streaming',
  'fun-asr-realtime',
  'paraformer-realtime-v2',
  'paraformer-realtime-v1',
]

const POLISH_MODES: { value: VoicePolishMode; label: string }[] = [
  { value: 'off', label: '关闭（只用 ASR）' },
  { value: 'error_fix', label: '只纠错（推荐）' },
  { value: 'rewrite', label: '允许改写' },
]

const EVENT_LABELS: Record<string, string> = {
  'room.join': '加入房间',
  'room.leave': '离开房间',
  'session.start': '开始录音',
  'session.stop': '结束录音',
  'session.discarded': '放弃录音',
  'asr.final': '识别完成',
  'polish.request': '请求纠错',
  'polish.result': '纠错完成',
  'polish.failed': '纠错失败',
  'polish.skipped': '纠错未采用',
  'polish.stale': '纠错过期',
  'segment.send': '下发文本',
  'segment.ack': '电脑已上屏',
  'segment.abandoned': '放弃替换',
  'segment.abort': '撤销段落',
  'desktop.inject_failed': '注入失败',
}

type Tab = 'config' | 'live' | 'segments' | 'events'

export function VoiceRoomDetailPage() {
  const { roomId = '' } = useParams()
  const [tab, setTab] = useState<Tab>('live')

  const liveQuery = useQuery({
    queryKey: ['voice-live', roomId],
    queryFn: () => api.voiceLive(roomId),
    enabled: Boolean(roomId),
    refetchInterval: tab === 'live' ? 3_000 : 15_000,
  })

  if (liveQuery.isLoading) return <div className="py-12 text-sm text-mist">正在加载…</div>
  if (liveQuery.isError || !liveQuery.data) {
    return (
      <div className="space-y-3 py-12">
        <p className="text-sm text-danger">{errorMessage(liveQuery.error, '房间不存在或加载失败')}</p>
        <Link to="/voice" className="text-sm text-signal">
          返回语音房列表
        </Link>
      </div>
    )
  }

  const { room, state, onlineClients, recentSegments, sessions, asrConfigured } = liveQuery.data

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/voice" className="mb-1 inline-flex items-center gap-1 text-xs text-mist hover:text-paper">
            <ArrowLeft size={13} /> 语音房
          </Link>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            {room.name}
            {state.busy && <Badge tone="ok">录音中</Badge>}
            {room.status !== 'active' && <Badge tone="warn">已停用</Badge>}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-3 text-sm text-mist">
            <span className="font-mono">{room.roomId}</span>
            <span className="flex items-center gap-1">
              <Smartphone size={13} /> {state.phones}
            </span>
            <span className="flex items-center gap-1">
              <Mic size={13} /> {state.desktops}
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="line" onClick={() => void liveQuery.refetch()}>
            <RefreshCw size={16} className={liveQuery.isFetching ? 'animate-spin' : ''} />
            刷新
          </Button>
        </div>
      </div>

      {!asrConfigured && (
        <Card className="flex items-start gap-3 border-warn/40 bg-warn/10">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-warn" />
          <div className="text-sm text-warn">
            服务端还没有配置阿里云语音识别 API Key（<code className="font-mono">ALIYUN_DASHSCOPE_API_KEY</code>
            ），手机端会提示“识别服务不可用”。请在 <code className="font-mono">.env</code> 里补上后重启后端。
          </div>
        </Card>
      )}

      <JoinCard roomId={room.roomId} joinCode={room.joinCode} requirePin={room.requirePin} />

      <div className="flex gap-1 overflow-x-auto border-b border-line">
        {(
          [
            ['live', '实时', Activity],
            ['config', '配置', Settings],
            ['segments', '识别日志', Sparkles],
            ['events', '事件时间线', Send],
          ] as [Tab, string, typeof Activity][]
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={cn(
              'flex min-h-11 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm transition',
              tab === value
                ? 'border-signal text-signal'
                : 'border-transparent text-mist hover:text-paper',
            )}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'live' && (
        <LiveTab
          onlineClients={onlineClients}
          recentSegments={recentSegments}
          sessions={sessions}
          busy={state.busy}
        />
      )}
      {tab === 'config' && <ConfigTab roomId={room.roomId} />}
      {tab === 'segments' && <SegmentsTab roomId={room.roomId} />}
      {tab === 'events' && <EventsTab roomId={room.roomId} />}
    </div>
  )
}

function JoinCard({ roomId, joinCode, requirePin }: { roomId: string; joinCode: string; requirePin: boolean }) {
  const phoneUrl = `${window.location.origin}/voice/join?code=${joinCode}`

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value)
      notifyOk(`${label}已复制`)
    } catch {
      notifyBad('复制失败，请手动输入')
    }
  }

  return (
    <Card className="grid gap-4 lg:grid-cols-[auto_1fr_auto] lg:items-center">
      <div className="text-center lg:text-left">
        <div className="text-xs uppercase tracking-[0.16em] text-mist">房间码</div>
        <div className="font-mono text-3xl tracking-[0.3em] text-signal">{joinCode}</div>
      </div>
      <div className="space-y-1 text-sm text-mist">
        <p>
          手机打开 <span className="text-paper">{window.location.origin}/voice/join</span> 输入房间码
          {requirePin ? '（本房间需要口令）' : ''}。
        </p>
        <p className="text-xs">
          电脑端需要跑 <code className="font-mono text-paper">voice-desktop</code>，用下面的令牌连到房间。
        </p>
      </div>
      <div className="flex flex-wrap gap-2 lg:flex-col">
        <Button variant="line" onClick={() => void copy(phoneUrl, '手机入口链接')}>
          <Copy size={15} />
          复制手机链接
        </Button>
        <DesktopTokenButton roomId={roomId} />
      </div>
    </Card>
  )
}

function DesktopTokenButton({ roomId }: { roomId: string }) {
  const [pending, setPending] = useState(false)

  async function issue() {
    setPending(true)
    try {
      const result = await api.issueVoiceToken(roomId)
      await navigator.clipboard.writeText(JSON.stringify(result.desktopConfig, null, 2))
      notifyOk('电脑端配置已复制，粘贴到 ~/.llm-gateway-voice.json')
    } catch (caught) {
      notifyBad(errorMessage(caught, '签发令牌失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Button variant="line" disabled={pending} onClick={() => void issue()}>
      {pending ? <Loader2 size={15} className="animate-spin" /> : <Copy size={15} />}
      复制电脑端配置
    </Button>
  )
}

function LiveTab({
  onlineClients,
  recentSegments,
  sessions,
  busy,
}: {
  onlineClients: { clientUid: string; role: string; name: string }[]
  recentSegments: {
    segId: string
    seq: number
    rev: number
    state: string
    rawText: string
    polishedText: string | null
    polishStatus: string | null
    polishMs: number | null
    ackCount: number
  }[]
  sessions: { id: number; clientName: string | null; status: string; audioMs: number; sentenceCount: number; startedAt: string | null }[]
  busy: boolean
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <Card className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-paper">最近识别（最近 30 段）</h2>
          {busy && <Badge tone="ok">正在录音</Badge>}
        </div>
        {recentSegments.length === 0 ? (
          <p className="py-8 text-center text-sm text-mist">还没有识别记录。让手机按住说话试试。</p>
        ) : (
          <div className="space-y-2">
            {recentSegments.map((segment) => (
              <div key={segment.segId} className="rounded-lg border border-line bg-panel-2/60 px-3 py-2">
                <div className="flex items-center gap-2 text-xs text-mist">
                  <span className="font-mono">#{segment.seq}</span>
                  {segment.rev >= 2 ? <Badge tone="ok">已纠错</Badge> : <Badge tone="mist">原文</Badge>}
                  {segment.polishStatus && segment.polishStatus !== 'ok' && (
                    <Badge tone="warn">{segment.polishStatus}</Badge>
                  )}
                  {segment.ackCount > 0 && (
                    <span className="flex items-center gap-0.5 text-signal">
                      <Check size={11} /> {segment.ackCount} 台上屏
                    </span>
                  )}
                  {segment.polishMs ? <span>{segment.polishMs}ms</span> : null}
                </div>
                <div className="mt-1 text-sm text-paper">{segment.polishedText || segment.rawText || '（空）'}</div>
                {segment.polishedText && segment.polishedText !== segment.rawText && (
                  <div className="mt-1 text-xs text-mist line-through">{segment.rawText}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="space-y-4">
        <Card className="space-y-2">
          <h2 className="text-sm font-medium text-paper">在线设备</h2>
          {onlineClients.length === 0 ? (
            <p className="text-sm text-mist">暂无设备连接</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {onlineClients.map((client) => (
                <li key={client.clientUid} className="flex items-center gap-2">
                  {client.role === 'phone' ? (
                    <Smartphone size={13} className="text-signal" />
                  ) : (
                    <Mic size={13} className="text-info" />
                  )}
                  <span className="text-paper">{client.name || client.clientUid}</span>
                  <span className="text-xs text-mist">{client.role === 'phone' ? '手机' : '电脑'}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="space-y-2">
          <h2 className="text-sm font-medium text-paper">最近会话</h2>
          {sessions.length === 0 ? (
            <p className="text-sm text-mist">还没有录音会话</p>
          ) : (
            <ul className="space-y-2 text-xs">
              {sessions.slice(0, 8).map((session) => (
                <li key={session.id} className="flex items-center justify-between gap-2">
                  <span className="truncate text-paper">{session.clientName || '未知设备'}</span>
                  <span className="shrink-0 text-mist">
                    {(session.audioMs / 1000).toFixed(1)}s · {session.sentenceCount} 句
                  </span>
                  <span className="shrink-0 text-mist">{formatTime(session.startedAt)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

function ConfigTab({ roomId }: { roomId: string }) {
  const queryClient = useQueryClient()
  const roomQuery = useQuery({ queryKey: ['voice-room', roomId], queryFn: () => api.voiceRoom(roomId) })
  const accountsQuery = useQuery({ queryKey: ['voice-polish-accounts'], queryFn: api.voicePolishAccounts })
  const [form, setForm] = useState<VoiceRoomUpdateInput & { pin?: string }>({})
  const [testText, setTestText] = useState('帮我看一下这个把，我们下周再讨论这个事情。')
  const [testResult, setTestResult] = useState<{ input: string; output: string; changed: boolean; ms: number; status: string; error: string | null } | null>(null)

  useEffect(() => {
    if (roomQuery.data) {
      setForm({
        name: roomQuery.data.name,
        asrModel: roomQuery.data.asrModel,
        disfluencyRemoval: roomQuery.data.disfluencyRemoval,
        maxRecordingSeconds: roomQuery.data.maxRecordingSeconds,
        polishMode: roomQuery.data.polishMode,
        polishAccountId: roomQuery.data.polishAccountId,
        polishModel: roomQuery.data.polishModel,
        polishSystemPrompt: roomQuery.data.polishSystemPrompt,
        polishTemperature: roomQuery.data.polishTemperature,
        logPartials: roomQuery.data.logPartials,
      })
    }
  }, [roomQuery.data])

  const save = useMutation({
    mutationFn: () => api.updateVoiceRoom(roomId, form),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-room', roomId] })
      void queryClient.invalidateQueries({ queryKey: ['voice-live', roomId] })
      void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
      notifyOk('配置已保存')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const rotate = useMutation({
    mutationFn: () => api.rotateVoiceCode(roomId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-live', roomId] })
      notifyOk('房间码已重置')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重置失败')),
  })

  const test = useMutation({
    mutationFn: () =>
      api.testVoicePolish({
        text: testText,
        roomId,
        accountId: form.polishAccountId ?? null,
        model: form.polishModel ?? null,
        mode: form.polishMode ?? 'error_fix',
      }),
    onSuccess: (result) => setTestResult(result),
    onError: (caught) => notifyBad(errorMessage(caught, '试跑失败')),
  })

  if (roomQuery.isLoading) return <div className="py-8 text-sm text-mist">正在加载配置…</div>
  if (!roomQuery.data) return <div className="py-8 text-sm text-danger">加载失败</div>

  const accounts = (accountsQuery.data?.items ?? []).filter((item) => item.available)
  const selected = accounts.find((item) => item.id === form.polishAccountId)

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-paper">基础</h2>
        <Field label="房间名称">
          <Input value={form.name ?? ''} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </Field>
        <Field label="新口令（留空则不修改）">
          <Input
            value={form.pin ?? ''}
            onChange={(event) => setForm({ ...form, pin: event.target.value })}
            placeholder="设置后手机加入需要输入"
          />
        </Field>
        <Field label="单次录音上限（秒）">
          <Input
            type="number"
            min={10}
            max={600}
            value={form.maxRecordingSeconds ?? 120}
            onChange={(event) => setForm({ ...form, maxRecordingSeconds: Number(event.target.value) || 120 })}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
            保存配置
          </Button>
          <Button
            variant="line"
            disabled={rotate.isPending}
            onClick={() => {
              if (window.confirm('重置后旧房间码立即失效，需要重新告诉手机。确定吗？')) rotate.mutate()
            }}
          >
            <RotateCcw size={15} />
            重置房间码
          </Button>
        </div>
      </Card>

      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-paper">语音识别（ASR）</h2>
        <Field label="识别模型">
          <Select
            value={form.asrModel ?? ''}
            onChange={(event) => setForm({ ...form, asrModel: event.target.value })}
          >
            {ASR_MODELS.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </Select>
        </Field>
        <label className="flex items-start gap-2 text-sm text-mist">
          <input
            type="checkbox"
            checked={form.disfluencyRemoval ?? true}
            onChange={(event) => setForm({ ...form, disfluencyRemoval: event.target.checked })}
            className="mt-0.5 h-4 w-4 accent-[var(--color-signal)]"
          />
          <span>
            自动删除语气词
            <span className="mt-0.5 block text-xs text-mist/80">
              实测关掉后「嗯/那个/就是说」一个都不会删；开启后 Qwen 能把语气词删干净并重组标点。
            </span>
          </span>
        </label>
      </Card>

      <Card className="space-y-4">
        <h2 className="text-sm font-medium text-paper">AI 纠错</h2>
        <Field label="档位">
          <Select
            value={form.polishMode ?? 'error_fix'}
            onChange={(event) => setForm({ ...form, polishMode: event.target.value as VoicePolishMode })}
          >
            {POLISH_MODES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
        {form.polishMode !== 'off' && (
          <>
            <Field label="上游账号">
              <Select
                value={form.polishAccountId == null ? '' : String(form.polishAccountId)}
                onChange={(event) =>
                  setForm({
                    ...form,
                    polishAccountId: event.target.value === '' ? null : Number(event.target.value),
                    polishModel: null,
                  })
                }
              >
                <option value="">不设置</option>
                {accounts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}（{item.provider}）
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="模型">
              <Select
                value={form.polishModel ?? ''}
                onChange={(event) => setForm({ ...form, polishModel: event.target.value || null })}
              >
                <option value="">账号默认{selected?.defaultModel ? `（${selected.defaultModel}）` : ''}</option>
                {(selected?.models ?? []).map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="温度">
              <Input
                type="number"
                step="0.1"
                min={0}
                max={1.5}
                value={form.polishTemperature ?? 0.2}
                onChange={(event) => setForm({ ...form, polishTemperature: Number(event.target.value) })}
              />
            </Field>
            <Field label="自定义提示词（留空用内置的只纠错提示词）">
              <textarea
                value={form.polishSystemPrompt ?? ''}
                onChange={(event) => setForm({ ...form, polishSystemPrompt: event.target.value || null })}
                rows={5}
                className="w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs text-paper"
                placeholder="留空即可。默认提示词只允许修正同音字/错别字/标点，禁止改写句式。"
              />
            </Field>
          </>
        )}
      </Card>

      <Card className="space-y-3">
        <h2 className="flex items-center gap-2 text-sm font-medium text-paper">
          <Wand2 size={15} />
          试跑纠错
        </h2>
        <p className="text-xs text-mist">
          用一段文字试试当前配置。下面会同时显示输入与模型输出，方便判断这一跳到底有没有价值。
        </p>
        <textarea
          value={testText}
          onChange={(event) => setTestText(event.target.value)}
          rows={3}
          className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
        />
        <Button variant="line" disabled={test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />}
          试跑
        </Button>
        {testResult && (
          <div className="space-y-2 rounded-lg border border-line bg-panel-2/60 p-3 text-sm">
            <div className="flex items-center gap-2 text-xs text-mist">
              <Badge tone={testResult.status === 'ok' ? 'ok' : 'warn'}>{testResult.status}</Badge>
              <span>{testResult.ms}ms</span>
              {testResult.changed ? <span className="text-signal">已修改</span> : <span>无改动</span>}
            </div>
            <div>
              <div className="text-xs text-mist">输入</div>
              <div className="text-paper">{testResult.input}</div>
            </div>
            <div>
              <div className="text-xs text-mist">模型输出</div>
              <div className="text-paper">{testResult.output}</div>
            </div>
            {testResult.error && <div className="text-xs text-danger">{testResult.error}</div>}
          </div>
        )}
      </Card>
    </div>
  )
}

function SegmentsTab({ roomId }: { roomId: string }) {
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [polishStatus, setPolishStatus] = useState('')

  const query = useMemo(
    () => ({ page, page_size: LOG_PAGE_SIZE, keyword: keyword || undefined, polish_status: polishStatus || undefined }),
    [page, keyword, polishStatus],
  )

  const segmentsQuery = useQuery({
    queryKey: ['voice-segments', roomId, query],
    queryFn: () => api.voiceSegments(roomId, query),
    refetchInterval: 5_000,
  })

  const items = segmentsQuery.data?.items ?? []
  const total = segmentsQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE))

  return (
    <div className="space-y-4">
      <Card className="grid grid-cols-2 gap-3 lg:flex lg:items-end">
        <Field label="关键字">
          <Input
            value={keyword}
            onChange={(event) => {
              setKeyword(event.target.value)
              setPage(1)
            }}
            placeholder="搜原文或纠错后文本"
          />
        </Field>
        <Field label="纠错状态">
          <Select
            value={polishStatus}
            onChange={(event) => {
              setPolishStatus(event.target.value)
              setPage(1)
            }}
          >
            <option value="">全部</option>
            <option value="ok">已纠错</option>
            <option value="no_change">无需修改</option>
            <option value="too_long">被判定过度改写</option>
            <option value="too_different">被判定过度改写</option>
            <option value="failed">纠错失败</option>
            <option value="none">没有纠错结果</option>
          </Select>
        </Field>
      </Card>

      <div className="hidden overflow-x-auto lg:block">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="bg-panel-2 text-xs uppercase tracking-[0.16em] text-mist">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">原文（ASR）</th>
              <th className="px-3 py-2 text-left">AI 纠错后</th>
              <th className="px-3 py-2 text-left">状态</th>
              <th className="px-3 py-2 text-left">模型</th>
              <th className="px-3 py-2 text-right">耗时</th>
              <th className="px-3 py-2 text-right">下发/回执</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {segmentsQuery.isLoading && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-mist">
                  加载中…
                </td>
              </tr>
            )}
            {!segmentsQuery.isLoading && items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-mist">
                  暂无记录
                </td>
              </tr>
            )}
            {items.map((item) => (
              <tr key={item.id} className="align-top">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-mist">{formatTime(item.createdAt)}</td>
                <td className="max-w-[280px] px-3 py-2 text-paper">{item.rawText || '—'}</td>
                <td className="max-w-[280px] px-3 py-2">
                  {item.polishedText ? (
                    <span className={item.polishedText === item.rawText ? 'text-mist' : 'text-signal'}>
                      {item.polishedText}
                    </span>
                  ) : (
                    <span className="text-mist">—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <PolishBadge status={item.polishStatus} rev={item.rev} />
                </td>
                <td className="max-w-[160px] truncate px-3 py-2 text-xs text-mist">{item.polishModel || '—'}</td>
                <td className="px-3 py-2 text-right text-xs text-mist">
                  {item.polishMs ? `${item.polishMs}ms` : '—'}
                </td>
                <td className="px-3 py-2 text-right text-xs text-mist">
                  {item.deliverCount} / {item.ackCount}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:hidden">
        {items.map((item) => (
          <Card key={item.id} className="space-y-2">
            <div className="flex items-center justify-between text-xs text-mist">
              <span>{formatTime(item.createdAt)}</span>
              <PolishBadge status={item.polishStatus} rev={item.rev} />
            </div>
            <div className="text-sm text-paper">{item.rawText || '（空）'}</div>
            {item.polishedText && item.polishedText !== item.rawText && (
              <div className="text-sm text-signal">{item.polishedText}</div>
            )}
            <div className="flex items-center justify-between text-xs text-mist">
              <span>{item.polishModel || '—'}</span>
              <span>
                {item.polishMs ? `${item.polishMs}ms` : ''} · 下发 {item.deliverCount} / 回执 {item.ackCount}
              </span>
            </div>
          </Card>
        ))}
      </div>

      <Pagination page={page} pageCount={pageCount} total={total} onPage={(next) => setPage(next)} />
    </div>
  )
}

function PolishBadge({ status, rev }: { status: string | null; rev: number }) {
  if (!status && rev >= 2) return <Badge tone="ok">已纠错</Badge>
  if (!status) return <Badge tone="mist">仅原文</Badge>
  if (status === 'ok') return <Badge tone="ok">已纠错</Badge>
  if (status === 'no_change') return <Badge tone="mist">无需修改</Badge>
  if (status === 'failed') return <Badge tone="bad">纠错失败</Badge>
  return <Badge tone="warn">{status}</Badge>
}

function EventsTab({ roomId }: { roomId: string }) {
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState('')

  const query = useMemo(() => ({ page, page_size: 50, kind: kind || undefined }), [page, kind])
  const eventsQuery = useQuery({
    queryKey: ['voice-events', roomId, query],
    queryFn: () => api.voiceEvents(roomId, query),
    refetchInterval: 5_000,
  })

  const items = eventsQuery.data?.items ?? []
  const total = eventsQuery.data?.total ?? 0
  const kinds = eventsQuery.data?.kinds ?? []

  return (
    <div className="space-y-4">
      <Card className="grid grid-cols-2 gap-3 lg:flex lg:items-end">
        <Field label="事件类型">
          <Select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value)
              setPage(1)
            }}
          >
            <option value="">全部</option>
            {kinds.map((item) => (
              <option key={item} value={item}>
                {EVENT_LABELS[item] ?? item}
              </option>
            ))}
          </Select>
        </Field>
      </Card>

      {items.length === 0 ? (
        <Card className="py-8 text-center text-sm text-mist">暂无事件</Card>
      ) : (
        <div className="space-y-1">
          {items.map((event) => (
            <div
              key={event.id}
              className={cn(
                'flex items-start gap-3 rounded-lg border px-3 py-2 text-sm',
                event.level === 'error'
                  ? 'border-danger/40 bg-danger/5'
                  : event.level === 'warn'
                    ? 'border-warn/30 bg-warn/5'
                    : 'border-line bg-panel/60',
              )}
            >
              <span className="w-32 shrink-0 text-xs text-mist">{formatTime(event.createdAt)}</span>
              <Badge tone={event.level === 'error' ? 'bad' : event.level === 'warn' ? 'warn' : 'mist'}>
                {EVENT_LABELS[event.kind] ?? event.kind}
              </Badge>
              <span className="min-w-0 flex-1 break-words text-paper">{event.message || '—'}</span>
            </div>
          ))}
        </div>
      )}

      <Pagination page={page} pageCount={Math.max(1, Math.ceil(total / 50))} total={total} onPage={setPage} />
    </div>
  )
}
