import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, UserRound } from 'lucide-react'
import { useMemo, useState } from 'react'

import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type AdminUser } from '../lib/api'
import { useSession } from '../lib/session'
import { Can } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

type Draft = { id: number | null; username: string; password: string; roleId: number | null; enabled: boolean }

// 卡片里的时间只保留「月-日 时:分」，完整时间放 title，避免移动端被截断
function shortTime(value: string | null | undefined) {
  const full = formatTime(value)
  return full.length >= 16 ? full.slice(5, 16) : full
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-line bg-panel/60 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-[0.14em] text-mist">{label}</div>
      <div className="mt-1 font-mono text-xl text-paper">{value}</div>
    </div>
  )
}

export function UsersPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [newPassword, setNewPassword] = useState('')

  const { me, can } = useSession()
  const { data: users = [], isPending } = useQuery({ queryKey: ['users'], queryFn: api.users })
  const { data: roles = [] } = useQuery({ queryKey: ['roles'], queryFn: api.roles })
  const isAdmin = me?.role?.builtin === true || (me?.permissions ?? []).includes('*')
  const assignableRoles = roles.filter((role) =>
    role.builtin ? isAdmin : role.permissions.every((code) => can(code)),
  )

  const stats = useMemo(
    () => ({
      total: users.length,
      enabled: users.filter((user) => user.is_enabled).length,
      admins: users.filter((user) => user.role_builtin).length,
    }),
    [users],
  )

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['users'] })
    void queryClient.invalidateQueries({ queryKey: ['roles'] })
  }

  const save = useMutation({
    mutationFn: (payload: Draft) => {
      if (payload.id) {
        return api.updateUser(payload.id, {
          username: payload.username,
          role_id: payload.roleId ?? undefined,
          is_enabled: payload.enabled,
        })
      }
      return api.createUser({
        username: payload.username,
        password: payload.password,
        role_id: payload.roleId as number,
        is_enabled: payload.enabled,
      })
    },
    onSuccess: () => {
      notifyOk('已保存')
      setDraft(null)
      invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '保存失败')),
  })

  const remove = useMutation({
    mutationFn: (userId: number) => api.deleteUser(userId),
    onSuccess: () => {
      notifyOk('已删除')
      invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '删除失败')),
  })

  const toggleStatus = useMutation({
    mutationFn: (user: AdminUser) => api.setUserStatus(user.id, !user.is_enabled),
    onSuccess: () => {
      notifyOk('已更新')
      invalidate()
    },
    onError: (error) => notifyBad(errorMessage(error, '更新失败')),
  })

  const resetPassword = useMutation({
    mutationFn: (payload: { id: number; password: string }) => api.resetUserPassword(payload.id, payload.password),
    onSuccess: () => {
      notifyOk('密码已重置')
      setResetFor(null)
      setNewPassword('')
    },
    onError: (error) => notifyBad(errorMessage(error, '重置失败')),
  })

  return (
    <PageContainer>
      <PageHeader
        title="用户管理"
        description="管理后台账号：每个账号绑定一个角色，权限由角色决定。"
        actions={
          <Can code="users:create">
            <Button
              className="w-full sm:w-auto"
              onClick={() =>
                setDraft({
                  id: null,
                  username: '',
                  password: '',
                  roleId: assignableRoles[0]?.id ?? null,
                  enabled: true,
                })
              }
            >
              <Plus size={16} /> 新增账号
            </Button>
          </Can>
        }
      />

      <div className="mt-4 grid grid-cols-3 gap-2 sm:gap-3">
        <Stat label="账号" value={stats.total} />
        <Stat label="启用" value={stats.enabled} />
        <Stat label="管理员" value={stats.admins} />
      </div>

      {isPending ? (
        <Card className="mt-4 p-6 text-sm text-mist">加载中…</Card>
      ) : users.length === 0 ? (
        <Card className="mt-4 p-6 text-sm text-mist">还没有账号</Card>
      ) : (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {users.map((user) => (
            <Card key={user.id} className="flex flex-col gap-3">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-signal/12 text-signal">
                    <UserRound size={16} />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-base font-medium text-paper" title={user.username}>
                      {user.username}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-mist" title={user.role_name || '未分配角色'}>
                      {user.role_name || '未分配角色'}
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {user.role_builtin ? <Badge tone="ok">管理员</Badge> : null}
                  {user.is_enabled ? <Badge tone="info">启用</Badge> : <Badge tone="bad">已停用</Badge>}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-[0.12em] text-mist">角色</div>
                  <div className="mt-1 truncate text-paper" title={user.role_name || '未分配角色'}>
                    {user.role_name || '未分配'}
                  </div>
                </div>
                <div className="rounded-lg border border-line bg-ink/40 px-3 py-2">
                  <div className="text-[11px] uppercase tracking-[0.12em] text-mist">最近登录</div>
                  <div className="mt-1 truncate text-paper" title={formatTime(user.last_login_at)}>
                    {user.last_login_at ? shortTime(user.last_login_at) : '从未登录'}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 border-t border-line pt-3 sm:flex sm:flex-wrap sm:justify-end">
                <Can code="users:update">
                  <Button
                    variant="line"
                    className="w-full sm:w-auto"
                    onClick={() =>
                      setDraft({
                        id: user.id,
                        username: user.username,
                        password: '',
                        roleId: user.role_id,
                        enabled: user.is_enabled,
                      })
                    }
                  >
                    编辑
                  </Button>
                </Can>
                <Can code="users:toggle_status">
                  <Button
                    variant="line"
                    className="w-full sm:w-auto"
                    onClick={() => toggleStatus.mutate(user)}
                  >
                    {user.is_enabled ? '停用' : '启用'}
                  </Button>
                </Can>
                <Can code="users:reset_password">
                  <Button variant="ghost" className="w-full sm:w-auto" onClick={() => setResetFor(user)}>
                    重置密码
                  </Button>
                </Can>
                <Can code="users:delete">
                  <Button
                    variant="danger"
                    className="w-full sm:w-auto"
                    onClick={() => {
                      if (window.confirm(`删除账号「${user.username}」？`)) remove.mutate(user.id)
                    }}
                  >
                    删除
                  </Button>
                </Can>
              </div>
            </Card>
          ))}
        </div>
      )}

      {draft ? (
        <Dialog title={draft.id ? '编辑账号' : '新增账号'} onClose={() => setDraft(null)}>
          <div className="space-y-4">
            <Field label="用户名">
              <Input
                value={draft.username}
                autoComplete="off"
                onChange={(event) => setDraft({ ...draft, username: event.target.value })}
              />
            </Field>
            {draft.id ? null : (
              <Field label="初始密码">
                <Input
                  type="password"
                  value={draft.password}
                  placeholder="至少 8 位"
                  autoComplete="new-password"
                  onChange={(event) => setDraft({ ...draft, password: event.target.value })}
                />
              </Field>
            )}
            <Field label="角色">
              <Select
                value={draft.roleId ?? ''}
                onChange={(event) => setDraft({ ...draft, roleId: Number(event.target.value) })}
              >
                <option value="">请选择角色</option>
                {assignableRoles.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                    {role.builtin ? '（全部权限）' : `（${role.permissions.length} 项权限）`}
                  </option>
                ))}
              </Select>
            </Field>
            <label className="flex items-center gap-2 rounded-lg border border-line bg-ink/40 px-3 py-2.5 text-sm text-mist">
              <input
                type="checkbox"
                className="accent-[var(--color-signal)]"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              启用该账号
            </label>
            <div className="grid grid-cols-2 gap-2 pt-1 sm:flex sm:justify-end">
              <Button variant="ghost" className="w-full sm:w-auto" onClick={() => setDraft(null)}>
                取消
              </Button>
              <Button
                className="w-full sm:w-auto"
                disabled={
                  save.isPending ||
                  !draft.username.trim() ||
                  draft.roleId === null ||
                  (!draft.id && draft.password.length < 8)
                }
                onClick={() => save.mutate(draft)}
              >
                保存
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}

      {resetFor ? (
        <Dialog title={`重置「${resetFor.username}」的密码`} onClose={() => setResetFor(null)}>
          <div className="space-y-4">
            <Field label="新密码">
              <Input
                type="password"
                value={newPassword}
                placeholder="至少 8 位"
                autoComplete="new-password"
                onChange={(event) => setNewPassword(event.target.value)}
              />
            </Field>
            <p className="text-xs text-mist">重置后该账号的登录会话会立即失效，需要重新登录。</p>
            <div className="grid grid-cols-2 gap-2 pt-1 sm:flex sm:justify-end">
              <Button variant="ghost" className="w-full sm:w-auto" onClick={() => setResetFor(null)}>
                取消
              </Button>
              <Button
                className="w-full sm:w-auto"
                disabled={resetPassword.isPending || newPassword.length < 8}
                onClick={() => resetPassword.mutate({ id: resetFor.id, password: newPassword })}
              >
                重置
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </PageContainer>
  )
}

export default UsersPage
