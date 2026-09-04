import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Link2, Mic, Plus, RadioTower, ScrollText, Settings2, Trash2 } from 'lucide-react'
import { api, type VoiceRoom } from '../lib/api'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { cn, errorMessage } from '../lib/utils'
import { notifyBad, notifyOk } from '../lib/toast'

function copyText(text: string, label = '已复制') {
  navigator.clipboard?.writeText(text).then(
    () => notifyOk(label),
    () => notifyBad('复制失败'),
  )
}

const LOG_KIND_TEXT: Record<string, string> = {
  transcribed: '转写完成',
  sent: '推送到桌面端',
  acked: '桌面端确认',
  error: '失败',
}
const LOG_KIND_TONE: Record<string, 'ok' | 'info' | 'warn' | 'bad'> = {
  transcribed: 'info',
  sent: 'warn',
  acked: 'ok',
  error: 'bad',
}

function RoomCard({ room }: { room: VoiceRoom }) {
  const queryClient = useQueryClient()
  const active = room.status === 'active'
  const [logsOpen, setLogsOpen] = useState(false)

  const toggleMutation = useMutation({
    mutationFn: () => api.updateVoiceRoom(room.id, { status: active ? 'closed' : 'active' }),
    onSuccess: () => {
      notifyOk(active ? '房间已关闭' : '房间已启用')
      void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '操作失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteVoiceRoom(room.id),
    onSuccess: () => {
      notifyOk('房间已删除')
      void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const nodeCommand = `cd voice-bridge && node src/index.js --server ${window.location.origin} --room ${room.code}`
  const mobileUrl = `${window.location.origin}/voice/mobile?room=${room.code}`

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-signal/10 text-signal">
            <RadioTower size={20} />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-lg font-semibold tracking-[0.2em]">{room.code}</span>
              <Badge tone={active ? 'ok' : 'bad'}>{active ? '启用' : '已关闭'}</Badge>
              <Badge tone={room.online_connections > 0 ? 'info' : 'mist'}>
                {room.online_connections > 0 ? `${room.online_connections} 台在线` : '无桌面端'}
              </Badge>
            </div>
            <div className="mt-1 truncate text-sm text-mist">{room.name}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="line"
            className="min-w-9 px-2"
            onClick={() => setLogsOpen(true)}
            title="查看日志"
          >
            <ScrollText size={16} />
            日志
          </Button>
          <Button
            variant="line"
            className="min-w-9 px-2"
            onClick={() => copyText(mobileUrl, '手机端链接已复制')}
            title="复制手机端链接"
          >
            <Link2 size={16} />
          </Button>
          <Button variant="line" className="min-w-9 px-2" onClick={() => toggleMutation.mutate()} title={active ? '关闭房间' : '启用房间'}>
            {active ? '关闭' : '启用'}
          </Button>
          <Button
            variant="danger"
            className="min-w-9 px-2"
            onClick={() => {
              if (window.confirm(`确认删除房间 ${room.code}？`)) deleteMutation.mutate()
            }}
            title="删除房间"
          >
            <Trash2 size={16} />
          </Button>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <div className="min-w-0 rounded-md border border-line bg-ink/60 p-3">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">手机端访问地址</div>
          <button
            type="button"
            onClick={() => copyText(mobileUrl, '手机端链接已复制')}
            className="mt-1 block w-full truncate text-left font-mono text-xs text-paper hover:text-signal"
            title={mobileUrl}
          >
            {mobileUrl}
          </button>
        </div>
        <div className="min-w-0 rounded-md border border-line bg-ink/60 p-3">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">桌面端小程序命令</div>
          <button
            type="button"
            onClick={() => copyText(nodeCommand, '桌面端命令已复制')}
            className="mt-1 block w-full truncate text-left font-mono text-xs text-paper hover:text-signal"
            title={nodeCommand}
          >
            {nodeCommand}
          </button>
        </div>
      </div>

      {room.recent_messages.length > 0 ? (
        <div className="max-h-44 space-y-2 overflow-y-auto rounded-md border border-line bg-ink/40 p-3">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">最近转写</div>
          {room.recent_messages.map((message) => (
            <div key={message.id} className="text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-mist">#{message.seq}</span>
                <span className="text-paper">{message.text}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-mist">
                <span>送达 {message.delivered_count}</span>
                <span className="inline-flex items-center gap-0.5">
                  <CheckCircle2 size={12} className="text-signal" />
                  确认 {message.acked_count}
                </span>
                {message.llm_model ? <span>优化: {message.llm_model}</span> : null}
                {message.client_ip ? <span className="truncate">来源 {message.client_ip}</span> : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {logsOpen ? (
        <Dialog title={`房间 ${room.code} 日志`} onClose={() => setLogsOpen(false)} className="w-full max-w-2xl">
          <div className="space-y-4">
            {room.recent_messages.length === 0 && room.recent_logs.length === 0 ? (
              <div className="rounded-md border border-line bg-ink/40 p-6 text-center text-sm text-mist">
                暂无日志。手机端录制并发送后，转写与推送记录会显示在这里。
              </div>
            ) : (
              <>
                <div className="rounded-md border border-line bg-ink/40 p-3">
                  <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">转写记录</div>
                  {room.recent_messages.length === 0 ? (
                    <div className="text-sm text-mist">暂无转写记录</div>
                  ) : (
                    <div className="space-y-2">
                      {room.recent_messages.map((message) => (
                        <div key={message.id} className="border-b border-line/50 pb-2 text-sm last:border-0 last:pb-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs text-mist">#{message.seq}</span>
                            <span className="break-words text-paper">{message.text}</span>
                          </div>
                          <div className="mt-1 break-words text-xs text-mist">原始：{message.raw_text}</div>
                          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-mist">
                            <span>送达 {message.delivered_count}</span>
                            <span className="inline-flex items-center gap-0.5">
                              <CheckCircle2 size={12} className="text-signal" />
                              确认 {message.acked_count}
                            </span>
                            {message.stt_model ? <span>STT: {message.stt_model}</span> : null}
                            {message.llm_model ? <span>LLM: {message.llm_model}</span> : null}
                            {message.client_ip ? <span>来源 {message.client_ip}</span> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="rounded-md border border-line bg-ink/40 p-3">
                  <div className="mb-2 text-xs uppercase tracking-[0.16em] text-mist">活动日志</div>
                  {room.recent_logs.length === 0 ? (
                    <div className="text-sm text-mist">暂无活动日志</div>
                  ) : (
                    <div className="space-y-1">
                      {room.recent_logs.map((log) => (
                        <div key={log.id} className="flex flex-wrap items-center gap-2 text-xs">
                          <Badge tone={LOG_KIND_TONE[log.kind] ?? 'mist'}>{LOG_KIND_TEXT[log.kind] ?? log.kind}</Badge>
                          {log.seq ? <span className="font-mono text-mist">#{log.seq}</span> : null}
                          <span className="min-w-0 flex-1 truncate text-mist">
                            {log.text || (log.detail && 'message' in log.detail ? String(log.detail.message) : '')}
                          </span>
                          {log.detail && 'delivered' in log.detail ? <span className="text-mist">→ {String(log.detail.delivered)} 台</span> : null}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </Dialog>
      ) : null}
    </Card>
  )
}

function SettingsDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['voice-settings'], queryFn: api.voiceSettings })
  const [form, setForm] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  function set(key: string, value: string) {
    setForm((previous) => ({ ...previous, [key]: value }))
  }

  async function save() {
    setPending(true)
    setError('')
    try {
      const sttId = form.stt_account_id !== undefined && form.stt_account_id !== ''
        ? Number(form.stt_account_id)
        : data?.stt_account_id ?? null
      const llmId = form.llm_account_id !== undefined && form.llm_account_id !== ''
        ? Number(form.llm_account_id)
        : data?.llm_account_id ?? null
      await api.saveVoiceSettings({
        stt_account_id: sttId,
        stt_model: form.stt_model ?? data?.stt_model,
        stt_language: form.stt_language ?? data?.stt_language ?? null,
        llm_account_id: llmId,
        llm_model: form.llm_model ?? data?.llm_model,
        llm_prompt: form.llm_prompt ?? data?.llm_prompt,
      })
      await queryClient.invalidateQueries({ queryKey: ['voice-settings'] })
      notifyOk('语音配置已保存')
      onClose()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  function accountLabel(option: { id: number; name: string; provider: string; has_credential: boolean }) {
    return `${option.name}（${option.provider}${option.has_credential ? '' : '，无凭据'}）`
  }

  return (
    <Dialog title="语音服务配置" onClose={onClose} className="w-full max-w-3xl">
      <div className="grid gap-4">
        <div className="rounded-lg border border-line bg-ink/40 p-3">
          <div className="mb-3 flex flex-wrap items-center gap-2 font-medium">
            <Mic size={16} className="text-signal" />
            语音识别（STT）
            <Badge tone={data?.stt_configured ? 'ok' : 'warn'}>{data?.stt_configured ? '已配置' : '未配置'}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="上游账号">
              <Select defaultValue={data?.stt_account_id ? String(data.stt_account_id) : ''} onChange={(event) => set('stt_account_id', event.target.value)}>
                <option value="">不使用上游账号</option>
                {(data?.stt_accounts ?? []).map((option) => (
                  <option key={option.id} value={String(option.id)}>{accountLabel(option)}</option>
                ))}
              </Select>
            </Field>
            <Field label="模型">
              <Input placeholder="whisper-1" defaultValue={data?.stt_model} onChange={(event) => set('stt_model', event.target.value)} />
            </Field>
            <Field label="语言（留空自动识别）">
              <Input placeholder="zh" defaultValue={data?.stt_language ?? ''} onChange={(event) => set('stt_language', event.target.value)} />
            </Field>
          </div>
        </div>

        <div className="rounded-lg border border-line bg-ink/40 p-3">
          <div className="mb-3 flex flex-wrap items-center gap-2 font-medium">
            <Settings2 size={16} className="text-signal" />
            大模型优化表达（LLM）
            <Badge tone={data?.llm_configured ? 'ok' : 'warn'}>{data?.llm_configured ? '已配置' : '未配置'}</Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="上游账号">
              <Select defaultValue={data?.llm_account_id ? String(data.llm_account_id) : ''} onChange={(event) => set('llm_account_id', event.target.value)}>
                <option value="">不使用上游账号</option>
                {(data?.llm_accounts ?? []).map((option) => (
                  <option key={option.id} value={String(option.id)}>{accountLabel(option)}</option>
                ))}
              </Select>
            </Field>
            <Field label="模型">
              <Input placeholder="gpt-4o-mini" defaultValue={data?.llm_model} onChange={(event) => set('llm_model', event.target.value)} />
            </Field>
          </div>
          <div className="mt-3">
            <Field label="优化提示词">
              <textarea
                rows={3}
                defaultValue={data?.llm_prompt}
                onChange={(event) => set('llm_prompt', event.target.value)}
                className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none placeholder:text-mist/70 focus:border-signal/70"
              />
            </Field>
          </div>
        </div>

        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button disabled={pending} onClick={() => void save()}>{pending ? '保存中…' : '保存配置'}</Button>
        </div>
      </div>
    </Dialog>
  )
}

export function VoicePage() {
  const queryClient = useQueryClient()
  const { data: rooms, isLoading } = useQuery({ queryKey: ['voice-rooms'], queryFn: api.voiceRooms })
  const { data: settings } = useQuery({ queryKey: ['voice-settings'], queryFn: api.voiceSettings })
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [newName, setNewName] = useState('')

  const createMutation = useMutation({
    mutationFn: () => api.createVoiceRoom({ name: newName.trim() }),
    onSuccess: (room) => {
      notifyOk(`房间 ${room.code} 已创建`)
      setCreateOpen(false)
      setNewName('')
      void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
      copyText(`${window.location.origin}/voice/mobile?room=${room.code}`, '手机端链接已复制')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '创建失败')),
  })

  const sttReady = settings?.stt_configured
  const llmReady = settings?.llm_configured

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">语音输入</h1>
          <p className="mt-1 text-sm text-mist">创建房间，手机按住说话，桌面端小程序自动把文字填入输入框。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="line" onClick={() => setSettingsOpen(true)}>
            <Settings2 size={16} />
            语音配置
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus size={16} />
            新建房间
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-line bg-panel/90 p-4">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">语音识别 STT</div>
          <div className={cn('mt-1 truncate text-lg font-semibold', sttReady ? 'text-signal' : 'text-warn')}>
            {sttReady ? '已配置' : '未配置'}
          </div>
          <div className="mt-1 truncate text-xs text-mist">{settings?.stt_account_name || '请选择上游账号'}</div>
        </div>
        <div className="rounded-xl border border-line bg-panel/90 p-4">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">大模型优化 LLM</div>
          <div className={cn('mt-1 truncate text-lg font-semibold', llmReady ? 'text-signal' : 'text-warn')}>
            {llmReady ? '已配置' : '未配置'}
          </div>
          <div className="mt-1 truncate text-xs text-mist">{settings?.llm_account_name || '请选择上游账号'}</div>
        </div>
        <div className="rounded-xl border border-line bg-panel/90 p-4">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">使用流程</div>
          <div className="mt-2 text-xs leading-relaxed text-mist">
            1. 新建房间 → 手机打开链接 → 输入房间码
            <br />
            2. 电脑运行桌面端小程序并加入房间
            <br />
            3. 手机按住说话，文字自动填入电脑输入框
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="text-sm text-mist">加载中…</div>
      ) : !rooms?.length ? (
        <Card className="flex flex-col items-center justify-center gap-3 py-12 text-center">
          <RadioTower size={36} className="text-mist" />
          <div className="font-medium">还没有房间</div>
          <div className="text-sm text-mist">点击「新建房间」创建第一个语音输入房间</div>
        </Card>
      ) : (
        <div className="grid gap-4">
          {rooms.map((room) => (
            <RoomCard key={room.id} room={room} />
          ))}
        </div>
      )}

      {createOpen ? (
        <Dialog title="新建语音输入房间" onClose={() => setCreateOpen(false)}>
          <div className="grid gap-3">
            <Field label="房间名称（可选）">
              <Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="例如：我的电脑" />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setCreateOpen(false)}>取消</Button>
              <Button disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>
                {createMutation.isPending ? '创建中…' : '创建房间'}
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}

      {settingsOpen ? <SettingsDialog onClose={() => setSettingsOpen(false)} /> : null}
    </div>
  )
}
