import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'

import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { Can, useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

type Draft = { id: number | null; name: string; permissions: Set<string> }

const EMPTY: Draft = { id: null, name: '', permissions: new Set() }

export function RolesPage() {
  const queryClient = useQueryClient()
  const canCreate = useCan('roles:create')
  const canUpdate = useCan('roles:update')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [keyword, setKeyword] = useState('')

  const { data: roles = [], isPending } = useQuery({ queryKey: ['roles'], queryFn: api.roles })
  const { data: catalog = [] } = useQuery({
    queryKey: ['permission-catalog'],
    queryFn: api.permissionCatalog,
    staleTime: 5 * 60_000,
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['roles'] })
    void queryClient.invalidateQueries({ queryKey: ['session', 'me'] })
  }

  const save = useMutation({
    mutationFn: (payload: Draft) =>
      payload.id
        ? api.updateRole(payload.id, { name: payload.name, permissions: [...payload.permissions] })
        : api.createRole({ name: payload.name, permissions: [...payload.permissions] }),
    onSuccess: () => {
      notifyOk('已保存')
      setDraft(null)
      invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '保存失败')),
  })

  const remove = useMutation({
    mutationFn: (roleId: number) => api.deleteRole(roleId),
    onSuccess: () => {
      notifyOk('已删除')
      invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '删除失败')),
  })

  const filtered = useMemo(() => {
    const q = keyword.trim().toLowerCase()
    if (!q) return catalog
    return catalog
      .map((group) => ({
        ...group,
        permissions: group.permissions.filter(
          (item) => item.label.toLowerCase().includes(q) || item.code.toLowerCase().includes(q),
        ),
      }))
      .filter((group) => group.permissions.length > 0)
  }, [catalog, keyword])

  function toggle(code: string) {
    setDraft((current) => {
      if (!current) return current
      const next = new Set(current.permissions)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return { ...current, permissions: next }
    })
  }

  function toggleGroup(codes: string[], selected: boolean) {
    setDraft((current) => {
      if (!current) return current
      const next = new Set(current.permissions)
      for (const code of codes) {
        if (selected) next.add(code)
        else next.delete(code)
      }
      return { ...current, permissions: next }
    })
  }

  return (
    <PageContainer>
      <PageHeader
        title="角色管理"
        description="一个账号绑定一个角色；角色决定能看到哪些入口、能点哪些按钮。"
        actions={
          <Can code="roles:create">
            <Button onClick={() => setDraft({ ...EMPTY, permissions: new Set() })}>
              <Plus size={16} /> 新增角色
            </Button>
          </Can>
        }
      />

      <Card className="mt-4 divide-y divide-line">
        {isPending ? (
          <div className="p-6 text-sm text-mist">加载中…</div>
        ) : roles.length === 0 ? (
          <div className="p-6 text-sm text-mist">还没有角色</div>
        ) : (
          roles.map((role) => (
            <div key={role.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                {role.builtin ? <ShieldCheck size={16} className="text-signal" /> : null}
                <span className="truncate text-sm font-medium text-paper">{role.name}</span>
                {role.builtin ? <Badge tone="ok">内置 · 全部权限</Badge> : null}
              </div>
              <span className="text-xs text-mist">
                {role.builtin ? '全部功能' : `${role.permissions.length} 项权限`} · {role.admin_count} 个账号
              </span>
              <div className="flex items-center gap-2">
                {role.builtin ? (
                  <span className="text-xs text-mist">不可修改</span>
                ) : (
                  <>
                    <Can code="roles:update">
                      <Button
                        variant="ghost"
                        onClick={() =>
                          setDraft({ id: role.id, name: role.name, permissions: new Set(role.permissions) })
                        }
                      >
                        编辑
                      </Button>
                    </Can>
                    <Can code="roles:delete">
                      <Button
                        variant="ghost"
                        onClick={() => {
                          if (window.confirm(`删除角色「${role.name}」？`)) remove.mutate(role.id)
                        }}
                      >
                        删除
                      </Button>
                    </Can>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </Card>

      {draft ? (
        <Dialog title={draft.id ? '编辑角色' : '新增角色'} onClose={() => setDraft(null)}>
          <div className="space-y-4">
            <Field label="角色名称">
              <Input
                value={draft.name}
                placeholder="例如：只读运维"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </Field>
            <Field label="权限">
              <Input
                value={keyword}
                placeholder="搜索页面或动作"
                onChange={(event) => setKeyword(event.target.value)}
              />
            </Field>
            <div className="max-h-[46vh] space-y-3 overflow-y-auto pr-1">
              {filtered.map((group) => {
                const codes = group.permissions.map((item) => item.code)
                const allSelected = codes.every((code) => draft.permissions.has(code))
                const locked = !canCreate && !canUpdate
                return (
                  <div key={group.resource} className="rounded-lg border border-line p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-sm text-paper">{group.label}</span>
                      <button
                        type="button"
                        disabled={locked}
                        className={cn('text-xs', allSelected ? 'text-signal' : 'text-mist hover:text-paper')}
                        onClick={() => toggleGroup(codes, !allSelected)}
                      >
                        {allSelected ? '取消全选' : '全选本页'}
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                      {group.permissions.map((item) => (
                        <label key={item.code} className="flex cursor-pointer items-center gap-1.5 text-xs text-mist">
                          <input
                            type="checkbox"
                            className="accent-[var(--color-signal)]"
                            checked={draft.permissions.has(item.code)}
                            onChange={() => toggle(item.code)}
                          />
                          {item.label}
                        </label>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setDraft(null)}>
                取消
              </Button>
              <Button
                disabled={save.isPending || !draft.name.trim()}
                onClick={() => save.mutate(draft)}
              >
                保存
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </PageContainer>
  )
}

export default RolesPage
