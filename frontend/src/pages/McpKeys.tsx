import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Eye, KeyRound, MoreHorizontal, Pencil, Plus, Power, PowerOff, Trash2 } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type McpKeyItem } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

function CapabilityPicker({
  options,
  selected,
  onChange,
}: {
  options: { id: string; label: string }[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <div className="space-y-2">
      {options.map((cap) => (
        <label key={cap.id} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={selected.includes(cap.id)}
            onChange={(event) => {
              onChange(
                event.target.checked ? [...selected, cap.id] : selected.filter((id) => id !== cap.id),
              )
            }}
          />
          {cap.label}
        </label>
      ))}
      {!options.length ? <div className="text-xs text-mist">暂无启用能力，请先在后端注册 Provider</div> : null}
    </div>
  )
}

function parseLimit(value: string): number | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number(trimmed)
  if (!Number.isFinite(parsed) || parsed < 0) return undefined
  return Math.floor(parsed)
}

function RateLimitFields({
  rpm,
  concurrency,
  onRpm,
  onConcurrency,
  disabled,
}: {
  rpm: string
  concurrency: string
  onRpm: (value: string) => void
  onConcurrency: (value: string) => void
  disabled?: boolean
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label="RPM 上限">
        <Input
          type="number"
          min={0}
          value={rpm}
          disabled={disabled}
          onChange={(event) => onRpm(event.target.value)}
          placeholder="400"
        />
      </Field>
      <Field label="并发上限">
        <Input
          type="number"
          min={0}
          value={concurrency}
          disabled={disabled}
          onChange={(event) => onConcurrency(event.target.value)}
          placeholder="200"
        />
      </Field>
      <div className="col-span-2 text-xs text-mist">每分钟请求数与同时在途请求数，0 表示不限</div>
    </div>
  )
}

type ActionItem = { key: string; inline: ReactNode; menu: ReactNode }

/**
 * 单行操作区：尽量多地放按钮，放不下的收进「更多」。
 * 仅用于移动端（容器占满整行）；桌面端直接平铺全部按钮。
 */
function OverflowActions({ actions }: { actions: ActionItem[] }) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const measureRef = useRef<HTMLDivElement | null>(null)
  const [count, setCount] = useState(actions.length)
  const [open, setOpen] = useState(false)
  const signature = actions.map((action) => action.key).join(',')

  useLayoutEffect(() => {
    const wrap = wrapRef.current
    const measure = measureRef.current
    if (!wrap || !measure) return
    const compute = () => {
      const available = wrap.clientWidth - 4
      if (available <= 0) return
      const nodes = Array.from(measure.children) as HTMLElement[]
      const moreWidth = nodes[nodes.length - 1]?.offsetWidth ?? 0
      const widths = nodes.slice(0, actions.length).map((node) => node.offsetWidth)
      const gap = 8
      const total = widths.reduce((sum, width) => sum + width, 0) + gap * Math.max(0, widths.length - 1)
      if (total <= available) {
        setCount(actions.length)
        return
      }
      let used = 0
      let fits = 0
      for (let index = 0; index < widths.length; index += 1) {
        const next = used + (index > 0 ? gap : 0) + widths[index]
        if (next + gap + moreWidth <= available) {
          used = next
          fits = index + 1
        } else {
          break
        }
      }
      setCount(fits)
    }
    compute()
    const observer = new ResizeObserver(compute)
    observer.observe(wrap)
    return () => observer.disconnect()
    // 只在动作集合变化时重新测量；宽度变化由 ResizeObserver 处理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])

  const visible = actions.slice(0, count)
  const overflow = actions.slice(count)

  return (
    <div className="relative min-w-0">
      <div ref={wrapRef} className="flex flex-nowrap gap-2 overflow-hidden">
        {visible.map((action) => (
          <div key={action.key} className="shrink-0">
            {action.inline}
          </div>
        ))}
        {overflow.length ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-panel-2 px-3 py-2 text-sm font-medium text-paper transition hover:border-mist/40 md:min-h-9"
          >
            <MoreHorizontal size={16} /> 更多
          </button>
        ) : null}
      </div>

      <div
        ref={measureRef}
        aria-hidden="true"
        className="pointer-events-none invisible absolute left-0 top-0 flex gap-2 overflow-hidden"
      >
        {actions.map((action) => (
          <div key={action.key} className="shrink-0">
            {action.inline}
          </div>
        ))}
        <div className="shrink-0">
          <span className="inline-flex min-h-11 items-center gap-1.5 whitespace-nowrap rounded-md border border-line px-3 py-2 text-sm font-medium md:min-h-9">
            <MoreHorizontal size={16} /> 更多
          </span>
        </div>
      </div>

      {open ? (
        <Dialog title="更多操作" onClose={() => setOpen(false)}>
          <div className="grid gap-2">
            {overflow.map((action) => (
              <div key={action.key}>{action.menu}</div>
            ))}
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

export function McpKeysPage() {
  const canCreate = useCan('mcp_keys:create')
  const canUpdate = useCan('mcp_keys:update')
  const canDelete = useCan('mcp_keys:delete')
  const canReveal = useCan('mcp_keys:reveal')
  const canDocs = useCan('mcp_catalog:view')
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [rpm, setRpm] = useState('400')
  const [concurrency, setConcurrency] = useState('200')
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [revealed, setRevealed] = useState<{ id: number; name: string; key: string } | null>(null)
  const [editing, setEditing] = useState<McpKeyItem | null>(null)
  const [editName, setEditName] = useState('')
  const [editSelected, setEditSelected] = useState<string[]>([])
  const [editRpm, setEditRpm] = useState('400')
  const [editConcurrency, setEditConcurrency] = useState('200')

  const catalog = useQuery({
    queryKey: ['mcp-catalog'],
    queryFn: () => api.mcpCatalog(),
  })
  const capabilityOptions = (catalog.data?.items || [])
    .filter((item) => item.status === 'enabled')
    .map((item) => ({ id: item.capability_id, label: `${item.capability_id} · ${item.name}` }))

  useEffect(() => {
    if (!selected.length && capabilityOptions.length) {
      setSelected([capabilityOptions[0].id])
    }
  }, [capabilityOptions, selected.length])

  const { data = [], refetch, isError, error } = useQuery({
    queryKey: ['mcp-keys'],
    queryFn: () => api.mcpKeys(),
  })

  const { data: liveUsage = [] } = useQuery({
    queryKey: ['mcp-key-usage'],
    queryFn: () => api.mcpKeyUsage().then((payload) => payload.items),
    refetchInterval: 10000,
  })
  const usageByKey: Record<number, { rpm: number; concurrency: number }> = {}
  for (const entry of liveUsage) {
    usageByKey[entry.id] = { rpm: entry.rpm_used, concurrency: entry.concurrency_used }
  }

  const createMutation = useMutation({
    mutationFn: () =>
      api.createMcpKey({
        name: name.trim(),
        capability_ids: selected,
        rpm_limit: parseLimit(rpm) ?? 0,
        concurrency_limit: parseLimit(concurrency) ?? 0,
      }),
    onSuccess: async (item) => {
      notifyOk('MCP Key 已创建')
      setCreatedKey(item.key || null)
      setName('')
      setRpm('400')
      setConcurrency('200')
      await queryClient.invalidateQueries({ queryKey: ['mcp-keys'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '创建失败')),
  })

  const toggleStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.updateMcpKey(id, { status }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['mcp-keys'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteMcpKey(id),
    onSuccess: async () => {
      notifyOk('已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-keys'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const reveal = useMutation({
    mutationFn: (id: number) => api.revealMcpKey(id),
    onSuccess: (item) => setRevealed(item),
    onError: (caught) => notifyBad(errorMessage(caught, '查看失败')),
  })

  const saveEdit = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error('未选择 MCP Key')
      const trimmed = editName.trim()
      const nameChanged = trimmed !== editing.name
      const current = [...editing.capability_ids].sort().join(',')
      const next = [...editSelected].sort().join(',')
      const capsChanged = current !== next
      const rpmValue = parseLimit(editRpm) ?? 0
      const concurrencyValue = parseLimit(editConcurrency) ?? 0
      const limitsChanged = rpmValue !== editing.rpm_limit || concurrencyValue !== editing.concurrency_limit
      if (!nameChanged && !capsChanged && !limitsChanged) return
      if (nameChanged || limitsChanged) {
        await api.updateMcpKey(editing.id, {
          ...(nameChanged ? { name: trimmed } : {}),
          ...(limitsChanged ? { rpm_limit: rpmValue, concurrency_limit: concurrencyValue } : {}),
        })
      }
      if (capsChanged) {
        await api.updateMcpKeyCapabilities(editing.id, editSelected)
      }
    },
    onSuccess: async () => {
      notifyOk('MCP Key 已更新')
      setEditing(null)
      await queryClient.invalidateQueries({ queryKey: ['mcp-keys'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '更新失败')),
  })

  function openEdit(item: McpKeyItem) {
    setEditing(item)
    setEditName(item.name)
    setEditSelected([...item.capability_ids])
    setEditRpm(String(item.rpm_limit))
    setEditConcurrency(String(item.concurrency_limit))
  }

  async function copyKey(key: string) {
    try {
      await navigator.clipboard.writeText(key)
      notifyOk('已复制')
    } catch {
      notifyBad('复制失败，请手动选择复制')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-mist">独立于聊天 sk- Key。完整密钥可随时查看并复制。</p>
{canCreate ? (
        <Button
          type="button"
          onClick={() => {
            setCreatedKey(null)
            setOpen(true)
          }}
        >
          <Plus size={16} /> 新建 MCP Key
        </Button>
) : null}
      </div>

      <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
        {data.map((item) => {
          const actions = ([
            canDocs
              ? {
                  key: 'docs',
                  inline: (
                    <Link
                      to={`/mcp-plaza/docs?key=${item.id}`}
                      className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-line bg-panel-2 px-3 py-2 text-sm font-medium text-paper transition hover:border-mist/40 md:min-h-9"
                    >
                      <ExternalLink size={14} /> 接入
                    </Link>
                  ),
                  menu: (
                    <Link
                      to={`/mcp-plaza/docs?key=${item.id}`}
                      className="inline-flex min-h-11 w-full items-center gap-2 rounded-md border border-line bg-panel-2 px-3 py-2 text-sm font-medium text-paper transition hover:border-mist/40"
                    >
                      <ExternalLink size={15} /> 接入说明
                    </Link>
                  ),
                }
              : null,
            canUpdate
              ? {
                  key: 'edit',
                  inline: (
                    <Button
                      type="button"
                      variant="line"
                      className="shrink-0 whitespace-nowrap"
                      onClick={() => openEdit(item)}
                    >
                      <Pencil size={14} /> 编辑
                    </Button>
                  ),
                  menu: (
                    <Button type="button" variant="line" className="w-full justify-start" onClick={() => openEdit(item)}>
                      <Pencil size={15} /> 编辑
                    </Button>
                  ),
                }
              : null,
            canReveal
              ? {
                  key: 'reveal',
                  inline: (
                    <Button
                      type="button"
                      variant="line"
                      className="shrink-0 whitespace-nowrap"
                      disabled={reveal.isPending}
                      onClick={() => reveal.mutate(item.id)}
                    >
                      <Eye size={14} /> 查看
                    </Button>
                  ),
                  menu: (
                    <Button
                      type="button"
                      variant="line"
                      className="w-full justify-start"
                      disabled={reveal.isPending}
                      onClick={() => reveal.mutate(item.id)}
                    >
                      <Eye size={15} /> 查看密钥
                    </Button>
                  ),
                }
              : null,
            canUpdate
              ? {
                  key: 'toggle',
                  inline: (
                    <button
                      type="button"
                      onClick={() =>
                        toggleStatus.mutate({ id: item.id, status: item.status === 'active' ? 'disabled' : 'active' })
                      }
                      className={
                        item.status === 'active'
                          ? 'inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-warn/40 bg-warn/10 px-3 py-1.5 text-sm font-medium text-warn transition hover:bg-warn/20 md:min-h-9'
                          : 'inline-flex min-h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-signal/40 bg-signal/10 px-3 py-1.5 text-sm font-medium text-signal transition hover:bg-signal/20 md:min-h-9'
                      }
                    >
                      {item.status === 'active' ? <PowerOff size={14} /> : <Power size={14} />}
                      {item.status === 'active' ? '停用' : '启用'}
                    </button>
                  ),
                  menu: (
                    <Button
                      type="button"
                      variant="line"
                      className={item.status === 'active' ? 'w-full justify-start text-warn' : 'w-full justify-start text-signal'}
                      onClick={() =>
                        toggleStatus.mutate({ id: item.id, status: item.status === 'active' ? 'disabled' : 'active' })
                      }
                    >
                      {item.status === 'active' ? <PowerOff size={15} /> : <Power size={15} />}
                      {item.status === 'active' ? '停用' : '启用'}
                    </Button>
                  ),
                }
              : null,
            canDelete
              ? {
                  key: 'delete',
                  inline: (
                    <Button
                      type="button"
                      variant="danger"
                      className="shrink-0 whitespace-nowrap"
                      onClick={() => {
                        if (window.confirm(`删除 ${item.name}？`)) remove.mutate(item.id)
                      }}
                    >
                      <Trash2 size={14} /> 删除
                    </Button>
                  ),
                  menu: (
                    <Button
                      type="button"
                      variant="danger"
                      className="w-full justify-start"
                      onClick={() => {
                        if (window.confirm(`删除 ${item.name}？`)) remove.mutate(item.id)
                      }}
                    >
                      <Trash2 size={15} /> 删除
                    </Button>
                  ),
                }
              : null,
          ] as (ActionItem | null)[]).filter((action): action is ActionItem => action !== null)

          return (
          <Card key={item.id} className="flex flex-col gap-3 p-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 font-medium">
                <KeyRound size={15} />
                <span className="min-w-0 break-words">{item.name}</span>
                <Badge tone={item.status === 'active' ? 'ok' : 'warn'}>
                  {item.status === 'active' ? '已启用' : '已停用'}
                </Badge>
              </div>
              <div className="mt-1 text-xs text-mist">
                {item.key_prefix}… · 能力 [{item.capability_ids.join(', ')}] · 创建{' '}
                {formatTime(item.created_at)}
              </div>
              <div className="mt-1 font-mono text-xs text-mist">
                RPM {usageByKey[item.id]?.rpm ?? item.rpm_used ?? 0} /{' '}
                {item.rpm_limit > 0 ? item.rpm_limit : '不限'} · 并发{' '}
                {usageByKey[item.id]?.concurrency ?? item.concurrency_used ?? 0} /{' '}
                {item.concurrency_limit > 0 ? item.concurrency_limit : '不限'}
              </div>
            </div>
            <OverflowActions actions={actions} />
          </Card>
          )
        })}
      </div>
      {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载失败')}</div> : null}
      {!data.length ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
          还没有 MCP Key
        </div>
      ) : null}

      {open ? (
        <Dialog
          title="新建 MCP Key"
          onClose={() => {
            setOpen(false)
            setCreatedKey(null)
          }}
        >
          <div className="grid gap-3">
            {createdKey ? (
              <div className="space-y-2">
                <div className="text-sm text-ok">完整密钥如下，之后可在列表点击「查看」再次获取：</div>
                <code className="block break-all rounded-md border border-line bg-ink p-3 text-xs">{createdKey}</code>
                <Button type="button" onClick={() => void copyKey(createdKey)}>
                  复制
                </Button>
              </div>
            ) : (
              <>
                <Field label="名称">
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="agent-prod" />
                </Field>
                <Field label="授权能力（来自服务目录注册表）">
                  <CapabilityPicker options={capabilityOptions} selected={selected} onChange={setSelected} />
                </Field>
                <RateLimitFields
                  rpm={rpm}
                  concurrency={concurrency}
                  onRpm={setRpm}
                  onConcurrency={setConcurrency}
                />
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                    取消
                  </Button>
                  <Button
                    type="button"
                    disabled={!name.trim() || !selected.length || createMutation.isPending}
                    onClick={() => createMutation.mutate()}
                  >
                    创建
                  </Button>
                </div>
              </>
            )}
          </div>
        </Dialog>
      ) : null}
      {editing ? (
        <Dialog title={`编辑 MCP Key · ${editing.name}`} onClose={() => (saveEdit.isPending ? null : setEditing(null))}>
          <div className="grid gap-3">
            <Field label="名称">
              <Input
                value={editName}
                onChange={(event) => setEditName(event.target.value)}
                placeholder="agent-prod"
                disabled={saveEdit.isPending}
              />
            </Field>
            <Field label="授权能力">
              <CapabilityPicker options={capabilityOptions} selected={editSelected} onChange={setEditSelected} />
            </Field>
            <p className="text-xs text-mist">取消勾选即收回该能力授权；至少保留一项。</p>
            <RateLimitFields
              rpm={editRpm}
              concurrency={editConcurrency}
              onRpm={setEditRpm}
              onConcurrency={setEditConcurrency}
              disabled={saveEdit.isPending}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={saveEdit.isPending} onClick={() => setEditing(null)}>
                取消
              </Button>
              <Button
                type="button"
                disabled={!editName.trim() || !editSelected.length || saveEdit.isPending}
                onClick={() => saveEdit.mutate()}
              >
                {saveEdit.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {revealed ? (
        <Dialog title={`查看 MCP Key · ${revealed.name}`} onClose={() => setRevealed(null)}>
          <div className="space-y-3">
            <code className="block break-all rounded-md border border-line bg-ink p-3 text-xs">{revealed.key}</code>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setRevealed(null)}>
                关闭
              </Button>
              <Button type="button" onClick={() => void copyKey(revealed.key)}>
                复制
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
      <button type="button" className="hidden" onClick={() => void refetch()} />
    </div>
  )
}

export default McpKeysPage
