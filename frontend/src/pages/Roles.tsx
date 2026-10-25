import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'

import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Field, Input, Sheet } from '../components/ui'
import { api } from '../lib/api'
import { useSession } from '../lib/session'
import { Can } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

type Draft = { id: number | null; name: string; permissions: Set<string> }

const EMPTY: Draft = { id: null, name: '', permissions: new Set() }

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel/60 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-[0.14em] text-mist">{label}</div>
      <div className="mt-1 font-mono text-xl text-paper">
        {value}
        {hint ? <span className="ml-1 text-xs text-mist">{hint}</span> : null}
      </div>
    </div>
  )
}

export function RolesPage() {
  const queryClient = useQueryClient()
  const { me, can } = useSession()
  const isAdmin = me?.role?.builtin === true || (me?.permissions ?? []).includes('*')
  const grantable = (code: string) => isAdmin || can(code)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [keyword, setKeyword] = useState('')

  const { data: roles = [], isPending } = useQuery({ queryKey: ['roles'], queryFn: api.roles })
  const { data: catalog = [] } = useQuery({
    queryKey: ['permission-catalog'],
    queryFn: api.permissionCatalog,
    staleTime: 5 * 60_000,
  })

  const totalCodes = useMemo(
    () => catalog.reduce((sum, group) => sum + group.permissions.length, 0),
    [catalog],
  )

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
          (item) =>
            item.label.toLowerCase().includes(q) ||
            item.code.toLowerCase().includes(q) ||
            group.label.toLowerCase().includes(q),
        ),
      }))
      .filter((group) => group.permissions.length > 0)
  }, [catalog, keyword])

  function toggle(code: string) {
    if (!grantable(code)) return
    setDraft((current) => {
      if (!current) return current
      const next = new Set(current.permissions)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return { ...current, permissions: next }
    })
  }

  function toggleGroup(codes: string[], selected: boolean) {
    const allowed = codes.filter(grantable)
    if (!allowed.length) return
    setDraft((current) => {
      if (!current) return current
      const next = new Set(current.permissions)
      for (const code of allowed) {
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
            <Button className="w-full sm:w-auto" onClick={() => setDraft({ ...EMPTY, permissions: new Set() })}>
              <Plus size={16} /> 新增角色
            </Button>
          </Can>
        }
      />

      <div className="mt-4 grid grid-cols-3 gap-2 sm:gap-3">
        <Stat label="角色" value={roles.length} />
        <Stat label="内置" value={roles.filter((role) => role.builtin).length} />
        <Stat label="可授权限" value={totalCodes} hint="项" />
      </div>

      {isPending ? (
        <Card className="mt-4 p-6 text-sm text-mist">加载中…</Card>
      ) : roles.length === 0 ? (
        <Card className="mt-4 p-6 text-sm text-mist">还没有角色</Card>
      ) : (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {roles.map((role) => (
            <Card key={role.id} className="flex flex-col gap-3">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span
                    className={cn(
                      'grid h-9 w-9 shrink-0 place-items-center rounded-lg',
                      role.builtin ? 'bg-signal/15 text-signal' : 'bg-white/5 text-mist',
                    )}
                  >
                    <ShieldCheck size={16} />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-base font-medium text-paper" title={role.name}>
                      {role.name}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-mist">
                      {role.builtin ? '内置角色 · 固定全部权限' : `${role.permissions.length} 项权限`}
                    </div>
                  </div>
                </div>
                <Badge tone={role.builtin ? 'ok' : 'mist'}>{role.builtin ? '全部权限' : '自定义'}</Badge>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-[0.12em] text-mist">权限项</div>
                  <div className="mt-1 font-mono text-paper">
                    {role.builtin ? totalCodes : role.permissions.length}
                  </div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-[0.12em] text-mist">绑定账号</div>
                  <div className="mt-1 font-mono text-paper">{role.admin_count}</div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 border-t border-line pt-3 sm:flex sm:flex-wrap sm:justify-end">
                {role.builtin ? (
                  <span className="col-span-2 text-xs text-mist sm:py-2">内置角色不可修改</span>
                ) : (
                  <>
                    <Can code="roles:update">
                      {isAdmin || role.permissions.every((code) => can(code)) ? (
                      <Button
                        variant="line"
                        className="w-full sm:w-auto"
                        onClick={() =>
                          setDraft({ id: role.id, name: role.name, permissions: new Set(role.permissions) })
                        }
                      >
                        编辑
                      </Button>
                      ) : null}
                    </Can>
                    <Can code="roles:delete">
                      {isAdmin || role.permissions.every((code) => can(code)) ? (
                      <Button
                        variant="danger"
                        className="w-full sm:w-auto"
                        onClick={() => {
                          if (window.confirm(`删除角色「${role.name}」？`)) remove.mutate(role.id)
                        }}
                      >
                        删除
                      </Button>
                      ) : null}
                    </Can>
                  </>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {draft ? (
        <Sheet
          title={draft.id ? '编辑角色' : '新增角色'}
          onClose={() => setDraft(null)}
          size="lg:max-w-2xl"
          bodyClassName="flex min-h-0 flex-col gap-4 overflow-hidden"
          footer={
            <div className="grid grid-cols-2 gap-2 sm:flex sm:justify-end">
              <Button variant="ghost" className="w-full sm:w-auto" onClick={() => setDraft(null)}>
                取消
              </Button>
              <Button
                className="w-full sm:w-auto"
                disabled={save.isPending || !draft.name.trim()}
                onClick={() => save.mutate(draft)}
              >
                保存
              </Button>
            </div>
          }
        >
          <div className="shrink-0 space-y-4">
            <Field label="角色名称">
              <Input
                value={draft.name}
                placeholder="例如：只读运维"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </Field>

            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-paper">权限</span>
                <span className="text-xs text-mist">
                  已选 <span className="font-mono text-signal">{draft.permissions.size}</span> / {totalCodes} 项
                </span>
              </div>
              <Input
                value={keyword}
                placeholder="搜索页面或动作，例如「导出」「MCP」"
                onChange={(event) => setKeyword(event.target.value)}
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto pr-1">
            {filtered.map((group) => {
              const codes = group.permissions.map((item) => item.code)
              const selectedCount = codes.filter((code) => draft.permissions.has(code)).length
              const allSelected = selectedCount === codes.length
              return (
                <div key={group.resource} className="rounded-xl border border-line bg-ink/30 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm text-paper">{group.label}</span>
                      <span className="shrink-0 font-mono text-[11px] text-mist">
                        {selectedCount}/{codes.length}
                      </span>
                    </div>
                    <button
                      type="button"
                      className={cn(
                        'shrink-0 rounded-full px-2 py-0.5 text-xs transition',
                        allSelected ? 'bg-signal/15 text-signal' : 'text-mist hover:bg-white/5 hover:text-paper',
                      )}
                      onClick={() => toggleGroup(codes, !allSelected)}
                    >
                      {allSelected ? '清空本页' : '全选本页'}
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {group.permissions.map((item) => {
                      const active = draft.permissions.has(item.code)
                      const allowed = grantable(item.code)
                      return (
                        <button
                          key={item.code}
                          type="button"
                          title={allowed ? item.code : `${item.code}（超出你的权限，无法授予）`}
                          disabled={!allowed}
                          onClick={() => toggle(item.code)}
                          className={cn(
                            'rounded-full border px-3 py-1.5 text-xs transition',
                            active
                              ? 'border-signal/60 bg-signal/12 text-signal'
                              : 'border-line text-mist hover:border-mist/40 hover:text-paper',
                            !allowed && 'cursor-not-allowed opacity-35 hover:border-line hover:text-mist',
                          )}
                        >
                          {item.label}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )
            })}
            {filtered.length === 0 ? (
              <div className="rounded-xl border border-line px-3 py-6 text-center text-xs text-mist">
                没有匹配的权限
              </div>
            ) : null}
          </div>
        </Sheet>
      ) : null}
    </PageContainer>
  )
}

export default RolesPage
