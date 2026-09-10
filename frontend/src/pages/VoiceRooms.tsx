import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Link2, Mic, Plus, RefreshCw, Smartphone, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api } from '../lib/api'
import type { VoicePolishAccount, VoicePolishMode, VoiceRoomSummary } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

const ASR_MODELS = [
  { value: 'qwen-audio-3.0-asr-flash-streaming', label: 'Qwen-Audio-3.0-ASR-Flash-Streaming（推荐）' },
  { value: 'fun-asr-realtime', label: 'Fun-ASR-Realtime（中英混说 / 噪声环境）' },
  { value: 'paraformer-realtime-v2', label: 'Paraformer-Realtime-V2（更省钱）' },
]

const POLISH_MODES: { value: VoicePolishMode; label: string; hint: string }[] = [
  { value: 'off', label: '关闭（只用 ASR）', hint: '最省、最快，不做任何 AI 处理' },
  { value: 'error_fix', label: '只纠错（推荐）', hint: '修正同音字/错别字/标点，不改写句式' },
  { value: 'rewrite', label: '允许改写', hint: '可把口语整理成书面语，适合会议纪要' },
]

export function VoiceRoomsPage() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)

  const roomsQuery = useQuery({
    queryKey: ['voice-rooms'],
    queryFn: api.voiceRooms,
    refetchInterval: 5_000,
  })
  const accountsQuery = useQuery({ queryKey: ['voice-polish-accounts'], queryFn: api.voicePolishAccounts })

  const removeRoom = useMutation({
    mutationFn: (roomId: string) => api.deleteVoiceRoom(roomId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
      notifyOk('房间已删除')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const items = roomsQuery.data?.items ?? []
  const totals = items.reduce(
    (acc, room) => ({
      segments: acc.segments + room.todaySegments,
      phones: acc.phones + room.online.phones,
      desktops: acc.desktops + room.online.desktops,
    }),
    { segments: 0, phones: 0, desktops: 0 },
  )

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">语音房</h1>
          <p className="mt-1 text-sm text-mist">
            手机按住说话 → 实时识别 → AI 纠错 → 自动填进电脑输入框。手机与电脑都要先加入同一个房间。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="line" onClick={() => void roomsQuery.refetch()}>
            <RefreshCw size={16} className={roomsQuery.isFetching ? 'animate-spin' : ''} />
            刷新
          </Button>
          <Button onClick={() => setCreating(true)}>
            <Plus size={16} />
            新建房间
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">房间数</div>
          <div className="mt-1 text-2xl font-semibold">{items.length}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">今日识别句数</div>
          <div className="mt-1 text-2xl font-semibold">{totals.segments}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">手机在线</div>
          <div className="mt-1 text-2xl font-semibold text-signal">{totals.phones}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">电脑在线</div>
          <div className="mt-1 text-2xl font-semibold text-info">{totals.desktops}</div>
        </Card>
      </div>

      {items.length === 0 && !roomsQuery.isFetching ? (
        <Card className="flex flex-col items-center gap-3 py-10 text-center">
          <Mic size={28} className="text-mist/60" />
          <p className="text-sm text-mist">还没有语音房。新建一个，然后让手机和电脑都加入它。</p>
          <Button onClick={() => setCreating(true)}>
            <Plus size={16} />
            新建房间
          </Button>
        </Card>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          <div className="hidden bg-panel-2 px-4 py-2 text-xs uppercase tracking-[0.16em] text-mist lg:grid lg:grid-cols-[1.6fr_0.7fr_0.9fr_0.9fr_1.4fr_auto] lg:gap-3">
            <span>房间</span>
            <span>房间码</span>
            <span>在线</span>
            <span>今日句数</span>
            <span>纠错模型</span>
            <span className="text-right">操作</span>
          </div>
          <div className="divide-y divide-line">
            {items.map((room) => (
              <RoomRow
                key={room.roomId}
                room={room}
                onDelete={() => {
                  if (window.confirm(`确定删除房间「${room.name}」？该房间的文字与日志会一并删除。`)) {
                    removeRoom.mutate(room.roomId)
                  }
                }}
              />
            ))}
          </div>
        </div>
      )}

      {creating && (
        <CreateRoomDialog
          accounts={accountsQuery.data?.items ?? []}
          onClose={() => setCreating(false)}
          onCreated={(roomId) => {
            setCreating(false)
            void queryClient.invalidateQueries({ queryKey: ['voice-rooms'] })
            notifyOk('房间已创建')
            window.location.href = `/voice/rooms/${roomId}`
          }}
        />
      )}
    </div>
  )
}

function RoomRow({ room, onDelete }: { room: VoiceRoomSummary; onDelete: () => void }) {
  const phoneUrl = `${window.location.origin}/voice/join?code=${room.joinCode}`

  async function copyPhoneLink() {
    try {
      await navigator.clipboard.writeText(phoneUrl)
      notifyOk('手机入口链接已复制')
    } catch {
      notifyBad('复制失败，请手动复制房间码')
    }
  }

  return (
    <div className="grid gap-2 px-4 py-3 lg:grid-cols-[1.6fr_0.7fr_0.9fr_0.9fr_1.4fr_auto] lg:items-center lg:gap-3">
      <div className="min-w-0">
        <Link to={`/voice/rooms/${room.roomId}`} className="font-medium text-paper hover:text-signal">
          {room.name}
        </Link>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-mist">
          <span className="font-mono">{room.roomId}</span>
          {room.status !== 'active' && <Badge tone="warn">已停用</Badge>}
          {room.requirePin && <Badge tone="info">有口令</Badge>}
          {room.online.busy && <Badge tone="ok">录音中</Badge>}
        </div>
      </div>
      <div className="font-mono text-lg tracking-[0.2em] text-paper">{room.joinCode}</div>
      <div className="flex items-center gap-3 text-sm">
        <span className={cn('flex items-center gap-1', room.online.phones > 0 ? 'text-signal' : 'text-mist')}>
          <Smartphone size={13} />
          {room.online.phones}
        </span>
        <span className={cn('flex items-center gap-1', room.online.desktops > 0 ? 'text-info' : 'text-mist')}>
          <Mic size={13} />
          {room.online.desktops}
        </span>
      </div>
      <div className="text-sm text-mist">{room.todaySegments} 句</div>
      <div className="truncate text-xs text-mist">
        {room.polishMode === 'off' ? (
          <span>未启用纠错</span>
        ) : (
          <span>
            {room.polishModel || '账号默认模型'}
            <span className="ml-1 text-mist/70">({room.polishMode === 'rewrite' ? '允许改写' : '只纠错'})</span>
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 lg:justify-end">
        <Button variant="ghost" onClick={() => void copyPhoneLink()} title="复制手机入口">
          <Link2 size={15} />
        </Button>
        <Link to={`/voice/rooms/${room.roomId}`}>
          <Button variant="ghost" title="进入房间">
            <ExternalLink size={15} />
          </Button>
        </Link>
        <Button variant="ghost" onClick={onDelete} title="删除房间">
          <Trash2 size={15} className="text-danger" />
        </Button>
      </div>
    </div>
  )
}

function CreateRoomDialog({
  accounts,
  onClose,
  onCreated,
}: {
  accounts: VoicePolishAccount[]
  onClose: () => void
  onCreated: (roomId: string) => void
}) {
  const [name, setName] = useState('我的语音房')
  const [pin, setPin] = useState('')
  const [asrModel, setAsrModel] = useState(ASR_MODELS[0].value)
  const [disfluencyRemoval, setDisfluencyRemoval] = useState(true)
  const [polishMode, setPolishMode] = useState<VoicePolishMode>('error_fix')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [model, setModel] = useState('')
  const [maxSeconds, setMaxSeconds] = useState(120)

  const availableAccounts = accounts.filter((item) => item.available)
  const selected = availableAccounts.find((item) => item.id === accountId)

  const create = useMutation({
    mutationFn: () =>
      api.createVoiceRoom({
        name,
        pin: pin.trim() || null,
        asrModel,
        disfluencyRemoval,
        maxRecordingSeconds: maxSeconds,
        polishMode,
        polishAccountId: polishMode === 'off' ? null : accountId === '' ? undefined : Number(accountId),
        polishModel: model.trim() || null,
      }),
    onSuccess: (room) => onCreated(room.roomId),
    onError: (caught) => notifyBad(errorMessage(caught, '创建失败')),
  })

  return (
    <Dialog title="新建语音房" onClose={onClose}>
      <div className="space-y-4">
        <Field label="房间名称">
          <Input value={name} onChange={(event) => setName(event.target.value.slice(0, 64))} />
        </Field>

        <Field label="房间口令（可选）">
          <Input
            value={pin}
            onChange={(event) => setPin(event.target.value.slice(0, 32))}
            placeholder="留空表示不需要口令"
          />
        </Field>

        <Field label="语音识别模型">
          <Select value={asrModel} onChange={(event) => setAsrModel(event.target.value)}>
            {ASR_MODELS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>

        <label className="flex items-center gap-2 text-sm text-mist">
          <input
            type="checkbox"
            checked={disfluencyRemoval}
            onChange={(event) => setDisfluencyRemoval(event.target.checked)}
            className="h-4 w-4 accent-[var(--color-signal)]"
          />
          自动删除语气词（嗯、那个、就是说）—— 实测关掉后 ASR 一个赘词都不删
        </label>

        <Field label="AI 纠错档位">
          <Select value={polishMode} onChange={(event) => setPolishMode(event.target.value as VoicePolishMode)}>
            {POLISH_MODES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </Select>
        </Field>
        <p className="-mt-2 text-xs text-mist">{POLISH_MODES.find((item) => item.value === polishMode)?.hint}</p>

        {polishMode !== 'off' && (
          <>
            <Field label="纠错用的上游账号">
              <Select
                value={accountId === '' ? '' : String(accountId)}
                onChange={(event) => {
                  setAccountId(event.target.value === '' ? '' : Number(event.target.value))
                  setModel('')
                }}
              >
                <option value="">自动选择第一个可用账号</option>
                {availableAccounts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}（{item.provider}）
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="纠错模型">
              <Select value={model} onChange={(event) => setModel(event.target.value)}>
                <option value="">账号默认模型{selected?.defaultModel ? `（${selected.defaultModel}）` : ''}</option>
                {(selected?.models ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
            </Field>
            <p className="-mt-2 text-xs text-mist">
              提示：推理型模型会先消耗大量 token 思考，长句可能变慢，且需要 2048 以上 token 预算。
            </p>
          </>
        )}

        <Field label="单次录音上限（秒）">
          <Input
            type="number"
            min={10}
            max={600}
            value={maxSeconds}
            onChange={(event) => setMaxSeconds(Number(event.target.value) || 120)}
          />
        </Field>

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="line" onClick={onClose}>
            取消
          </Button>
          <Button disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? '创建中…' : '创建'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
