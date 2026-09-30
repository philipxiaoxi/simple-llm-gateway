import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Pencil, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type McpKeyItem } from '../lib/api'
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

export function McpKeysPage() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [createdKey, setCreatedKey] = useState<string | null>(null)
  const [revealed, setRevealed] = useState<{ id: number; name: string; key: string } | null>(null)
  const [editing, setEditing] = useState<McpKeyItem | null>(null)
  const [editName, setEditName] = useState('')
  const [editSelected, setEditSelected] = useState<string[]>([])

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

  const createMutation = useMutation({
    mutationFn: () => api.createMcpKey({ name: name.trim(), capability_ids: selected }),
    onSuccess: async (item) => {
      notifyOk('MCP Key 已创建')
      setCreatedKey(item.key || null)
      setName('')
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
      if (!nameChanged && !capsChanged) return
      if (nameChanged) {
        await api.updateMcpKey(editing.id, { name: trimmed })
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
        <Button
          type="button"
          onClick={() => {
            setCreatedKey(null)
            setOpen(true)
          }}
        >
          <Plus size={16} /> 新建 MCP Key
        </Button>
      </div>

      <div className="grid gap-3">
        {data.map((item) => (
          <Card key={item.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
            <div>
              <div className="flex items-center gap-2 font-medium">
                <KeyRound size={15} /> {item.name}
              </div>
              <div className="mt-1 text-xs text-mist">
                {item.key_prefix}… · {item.status} · 能力 [{item.capability_ids.join(', ')}] · 创建{' '}
                {formatTime(item.created_at)}
              </div>
            </div>
            <div className="flex gap-2">
              <Link
                to={`/mcp-plaza/docs?key=${item.id}`}
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md border border-line bg-panel-2 px-3 py-2 text-sm font-medium text-paper transition hover:border-mist/40 md:min-h-9"
              >
                接入
              </Link>
              <Button type="button" variant="line" onClick={() => openEdit(item)}>
                <Pencil size={14} /> 编辑
              </Button>
              <Button
                type="button"
                variant="line"
                disabled={reveal.isPending}
                onClick={() => reveal.mutate(item.id)}
              >
                查看
              </Button>
              <Button
                type="button"
                variant="line"
                onClick={() =>
                  toggleStatus.mutate({ id: item.id, status: item.status === 'active' ? 'disabled' : 'active' })
                }
              >
                {item.status === 'active' ? '停用' : '启用'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  if (window.confirm(`删除 ${item.name}？`)) remove.mutate(item.id)
                }}
              >
                删除
              </Button>
            </div>
          </Card>
        ))}
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
