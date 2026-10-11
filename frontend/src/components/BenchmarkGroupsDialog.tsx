import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { BenchmarkModelPicker } from './BenchmarkModelPicker'
import { Button, Dialog, Input } from './ui'
import { api, type BenchmarkGroup, type BenchmarkGroupMember } from '../lib/api'
import { targetsFromAccounts } from '../lib/benchmark'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, formatTime } from '../lib/utils'

function memberKey(member: BenchmarkGroupMember) {
  return `${member.account_id}:${member.model}`
}

export function BenchmarkGroupsDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: groupsData } = useQuery({ queryKey: ['benchmark-groups'], queryFn: api.benchmarkGroups })
  const groups = groupsData?.items ?? []
  const targets = targetsFromAccounts(accounts)

  const [newName, setNewName] = useState('')
  const [renameId, setRenameId] = useState<number | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [editing, setEditing] = useState<{ id: number; name: string; keys: string[] } | null>(null)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['benchmark-groups'] })

  const createMutation = useMutation({
    mutationFn: (name: string) => api.createBenchmarkGroup({ name, targets: [] }),
    onSuccess: (group) => {
      setNewName('')
      notifyOk(`已创建分组「${group.name}」，请添加要测速的模型`)
      void invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '创建分组失败')),
  })

  const renameMutation = useMutation({
    mutationFn: (id: number) => api.updateBenchmarkGroup(id, { name: renameDraft.trim() }),
    onSuccess: () => {
      setRenameId(null)
      notifyOk('分组已重命名')
      void invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '重命名失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteBenchmarkGroup(id),
    onSuccess: () => {
      notifyOk('分组已删除')
      void invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '删除分组失败')),
  })

  const saveTargetsMutation = useMutation({
    mutationFn: ({ id, keys }: { id: number; keys: string[] }) =>
      api.updateBenchmarkGroup(id, {
        targets: keys.map((key) => {
          const index = key.indexOf(':')
          return { account_id: Number(key.slice(0, index)), model: key.slice(index + 1) }
        }),
      }),
    onSuccess: () => {
      notifyOk('分组成员已更新')
      void invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '更新成员失败')),
  })

  function openEditor(group: BenchmarkGroup) {
    setEditing({ id: group.id, name: group.name, keys: group.members.map(memberKey) })
  }
  function saveEditor() {
    if (!editing) return
    const current = editing
    setEditing(null)
    saveTargetsMutation.mutate({ id: current.id, keys: current.keys })
  }
  function removeMember(group: BenchmarkGroup, member: BenchmarkGroupMember) {
    const keys = group.members.map(memberKey).filter((key) => key !== memberKey(member))
    saveTargetsMutation.mutate({ id: group.id, keys })
  }

  return (
    <Dialog title="分组管理" className="my-6 max-h-[80vh] max-w-3xl overflow-y-auto lg:my-[12vh]" onClose={onClose}>
      <div className="space-y-4">
        <div className="border border-line bg-panel-2/60 p-3">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">新建分组</div>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Input
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="分组名称，例如：主力生产模型"
              maxLength={128}
            />
            <Button
              className="shrink-0"
              disabled={createMutation.isPending || !newName.trim()}
              onClick={() => createMutation.mutate(newName.trim())}
            >
              <Plus size={16} />
              新建
            </Button>
          </div>
          <div className="mt-2 text-[11px] text-mist">先建空分组，再点「添加 / 编辑模型」维护要测速的模型；模型下架后会自动移除。</div>
        </div>

        <div className="grid gap-3">
          {groups.map((group) => {
            const unavailable = group.member_count - group.available_count
            const renaming = renameId === group.id
            return (
              <div key={group.id} className="overflow-hidden border border-line">
                <div className="flex items-center justify-between gap-2 border-b border-line/70 bg-panel-2/40 px-3 py-2.5">
                  {renaming ? (
                    <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center">
                      <Input value={renameDraft} onChange={(event) => setRenameDraft(event.target.value)} maxLength={128} />
                      <div className="flex shrink-0 gap-2">
                        <Button
                          disabled={renameMutation.isPending || !renameDraft.trim()}
                          onClick={() => renameMutation.mutate(group.id)}
                        >
                          保存
                        </Button>
                        <Button variant="line" onClick={() => setRenameId(null)}>取消</Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium" title={group.name}>{group.name}</div>
                        <div className="mt-0.5 truncate text-[11px] text-mist" title={`更新于 ${formatTime(group.updated_at)}`}>
                          {group.member_count} 个模型 · {group.available_count} 可用
                          {unavailable > 0 ? <span className="text-warn"> · {unavailable} 不可用</span> : null}
                          {' · '}更新于 {formatTime(group.updated_at)}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <Button variant="line" className="px-2.5" onClick={() => openEditor(group)}>
                          <Plus size={14} />
                          添加模型
                        </Button>
                        <button
                          type="button"
                          className="p-2 text-mist hover:text-paper"
                          onClick={() => {
                            setRenameId(group.id)
                            setRenameDraft(group.name)
                          }}
                          aria-label={`重命名 ${group.name}`}
                        >
                          <Pencil size={15} />
                        </button>
                        <button
                          type="button"
                          className="p-2 text-mist hover:text-danger"
                          disabled={deleteMutation.isPending}
                          onClick={() => deleteMutation.mutate(group.id)}
                          aria-label={`删除 ${group.name}`}
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </>
                  )}
                </div>
                {group.members.length ? (
                  <div className="divide-y divide-line/60">
                    {group.members.map((member) => (
                      <div key={memberKey(member)} className="flex items-center gap-3 px-3 py-2 text-xs">
                        <span className={cn('h-2 w-2 shrink-0 rounded-full', member.available ? 'bg-signal' : 'bg-warn')} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-paper" title={member.model}>{member.model}</span>
                          <span className="block truncate text-[10px] text-mist" title={member.account_name}>
                            {member.account_name || '未知账号'} · {member.provider || '—'}
                            {member.available ? '' : ` · ${member.reason}`}
                          </span>
                        </span>
                        <button
                          type="button"
                          className="shrink-0 text-mist hover:text-danger"
                          onClick={() => removeMember(group, member)}
                          aria-label={`从分组移除 ${member.model}`}
                        >
                          <X size={15} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="px-3 py-4 text-center text-xs text-mist">该分组还没有模型，点击「添加模型」开始配置。</div>
                )}
              </div>
            )
          })}
          {!groups.length ? <div className="px-3 py-8 text-center text-sm text-mist">还没有分组，先新建一个再添加模型</div> : null}
        </div>
      </div>

      {editing ? (
        <BenchmarkModelPicker
          targets={targets}
          selected={editing.keys}
          onChange={(keys) => setEditing((current) => (current ? { ...current, keys } : current))}
          onClose={saveEditor}
        />
      ) : null}
    </Dialog>
  )
}
