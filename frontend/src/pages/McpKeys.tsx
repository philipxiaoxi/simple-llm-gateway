import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

export function McpKeysPage() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [createdKey, setCreatedKey] = useState<string | null>(null)

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-mist">独立于聊天 sk- Key。创建后完整密钥只展示一次。</p>
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
                <div className="text-sm text-ok">请立即复制，之后无法再查看完整密钥：</div>
                <code className="block break-all rounded-md border border-line bg-ink p-3 text-xs">{createdKey}</code>
                <Button
                  type="button"
                  onClick={async () => {
                    await navigator.clipboard.writeText(createdKey)
                    notifyOk('已复制')
                  }}
                >
                  复制
                </Button>
              </div>
            ) : (
              <>
                <Field label="名称">
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="agent-prod" />
                </Field>
                <Field label="授权能力（来自服务目录注册表）">
                  <div className="space-y-2">
                    {capabilityOptions.map((cap) => (
                      <label key={cap.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selected.includes(cap.id)}
                          onChange={(e) => {
                            setSelected((prev) =>
                              e.target.checked ? [...prev, cap.id] : prev.filter((id) => id !== cap.id),
                            )
                          }}
                        />
                        {cap.label}
                      </label>
                    ))}
                    {!capabilityOptions.length ? (
                      <div className="text-xs text-mist">暂无启用能力，请先在后端注册 Provider</div>
                    ) : null}
                  </div>
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
      <button type="button" className="hidden" onClick={() => void refetch()} />
    </div>
  )
}

export default McpKeysPage
