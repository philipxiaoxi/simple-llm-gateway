import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useMemo, useState } from 'react'

import { PageContainer, PageHeader } from '../components/layout-primitives'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type AdminUser } from '../lib/api'
import { Can } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

type Draft = { id: number | null; username: string; password: string; roleId: number | null; enabled: boolean }

export function UsersPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [resetFor, setResetFor] = useState<AdminUser | null>(null)
  const [newPassword, setNewPassword] = useState('')

  const { data: users = [], isPending } = useQuery({ queryKey: ['users'], queryFn: api.users })
  const { data: roles = [] } = useQuery({ queryKey: ['roles'], queryFn: api.roles })

  const assignableRoles = useMemo(
    () => roles.filter((role) => role.builtin || role.permissions.length > 0 || role.id > 0),
    [roles],
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
              onClick={() =>
                setDraft({ id: null, username: '', password: '', roleId: roles[0]?.id ?? null, enabled: true })
              }
            >
              <Plus size={16} /> 新增账号
            </Button>
          </Can>
        }
      />

      <Card className="mt-4 divide-y divide-line">
        {isPending ? (
          <div className="p-6 text-sm text-mist">加载中…</div>
        ) : users.length === 0 ? (
          <div className="p-6 text-sm text-mist">还没有账号</div>
        ) : (
          users.map((user) => (
            <div key={user.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <span className="truncate text-sm font-medium text-paper">{user.username}</span>
                <Badge tone={user.role_builtin ? 'ok' : 'mist'}>{user.role_name || '未分配角色'}</Badge>
                {user.is_enabled ? null : <Badge tone="bad">已停用</Badge>}
              </div>
              <span className="text-xs text-mist">
                最近登录：{user.last_login_at ? formatTime(user.last_login_at) : '从未'}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <Can code="users:reset_password">
                  <Button variant="ghost" onClick={() => setResetFor(user)}>
                    重置密码
                  </Button>
                </Can>
                <Can code="users:toggle_status">
                  <Button variant="ghost" onClick={() => toggleStatus.mutate(user)}>
                    {user.is_enabled ? '停用' : '启用'}
                  </Button>
                </Can>
                <Can code="users:update">
                  <Button
                    variant="ghost"
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
                <Can code="users:delete">
                  <Button
                    variant="danger"
                    onClick={() => {
                      if (window.confirm(`删除账号「${user.username}」？`)) remove.mutate(user.id)
                    }}
                  >
                    删除
                  </Button>
                </Can>
              </div>
            </div>
          ))
        )}
      </Card>

      {draft ? (
        <Dialog title={draft.id ? '编辑账号' : '新增账号'} onClose={() => setDraft(null)}>
          <div className="space-y-4">
            <Field label="用户名">
              <Input
                value={draft.username}
                onChange={(event) => setDraft({ ...draft, username: event.target.value })}
              />
            </Field>
            {draft.id ? null : (
              <Field label="初始密码">
                <Input
                  type="password"
                  value={draft.password}
                  placeholder="至少 8 位"
                  onChange={(event) => setDraft({ ...draft, password: event.target.value })}
                />
              </Field>
            )}
            <Field label="角色">
              <Select
                value={draft.roleId ?? ''}
                onChange={(event) => setDraft({ ...draft, roleId: Number(event.target.value) })}
              >
                <option value="">请选择</option>
                {assignableRoles.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.name}
                    {role.builtin ? '（全部权限）' : ''}
                  </option>
                ))}
              </Select>
            </Field>
            <label className="flex items-center gap-2 text-sm text-mist">
              <input
                type="checkbox"
                className="accent-[var(--color-signal)]"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              启用该账号
            </label>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setDraft(null)}>
                取消
              </Button>
              <Button
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
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setResetFor(null)}>
              取消
            </Button>
            <Button
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
