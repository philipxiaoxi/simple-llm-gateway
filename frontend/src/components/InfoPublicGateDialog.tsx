import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { History, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type PublicGateScope } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'
import { InfoPublicSessionsDialog } from './InfoPublicSessionsDialog'
import { Button, Dialog, Field, Input, Switch } from './ui'

const settingsKey = (scope: PublicGateScope) => ['public-gate-settings', scope] as const

export function InfoPublicGateDialog({
  open,
  onClose,
  scope,
}: {
  open: boolean
  onClose: () => void
  scope: PublicGateScope
}) {
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: settingsKey(scope),
    queryFn: () => api.adminPublicGate(scope),
    enabled: open,
  })
  const [enabled, setEnabled] = useState(false)
  const [password, setPassword] = useState('')
  const [sessionsOpen, setSessionsOpen] = useState(false)

  useEffect(() => {
    if (open && status.data) setEnabled(status.data.required)
  }, [open, status.data])

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: settingsKey(scope) })
  }

  const save = useMutation({
    mutationFn: () => {
      const payload: { enabled: boolean; password?: string } = {
        // 设置口令是明确的开启动作，避免“填了口令却仍关闭”
        enabled: enabled || Boolean(password.trim()),
      }
      if (password.trim()) payload.password = password.trim()
      return api.adminPublicGateUpdate(scope, payload)
    },
    onSuccess: async (data) => {
      setPassword('')
      notifyOk(data.required ? '门禁已启用' : '门禁已更新')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const clear = useMutation({
    mutationFn: () => api.adminPublicGateUpdate(scope, { clear_password: true }),
    onSuccess: async () => {
      setPassword('')
      setEnabled(false)
      notifyOk('口令已清除，公开页恢复匿名访问')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '清除失败')),
  })

  const pending = save.isPending || clear.isPending
  const hasPassword = status.data?.has_password ?? false
  const willHavePassword = hasPassword || Boolean(password.trim())
  const ttlDays = status.data?.ttl_days ?? 3

  return (
    <Dialog
      title={scope === 'offline' ? '离线下载门禁' : '资讯公开页门禁'}
      onClose={() => (pending ? undefined : onClose())}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-paper">
          <Lock size={16} className="text-signal" /> 口令访问控制
        </div>
        <p className="text-xs text-mist">
          开启后，访问公开页需输入口令；验证通过后 {ttlDays} 天内免再输入，过期或改口令后需重新输入。
        </p>

        {!status.data ? (
          <div className="text-sm text-mist">{status.isError ? errorMessage(status.error, '加载失败') : '加载中…'}</div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
              <div>
                <div className="text-sm text-paper">启用访问门禁</div>
                <div className="text-xs text-mist">
                  {hasPassword ? '关闭后公开页恢复匿名访问，口令保留。' : '需先设置口令才能启用。'}
                </div>
              </div>
              <Switch
                checked={enabled}
                onCheckedChange={setEnabled}
                disabled={pending || (!hasPassword && !password.trim())}
              />
            </div>

            <Field label={hasPassword ? '访问口令（留空则不修改）' : '访问口令（至少 6 位）'}>
              <Input
                type="password"
                value={password}
                autoComplete="new-password"
                placeholder={hasPassword ? '••••••' : '设置一个口令'}
                onChange={(event) => setPassword(event.target.value)}
                disabled={pending}
              />
            </Field>

            {status.data.updated_at ? (
              <div className="text-xs text-mist">最近更新：{formatTime(status.data.updated_at)}</div>
            ) : null}
            {status.data.password_fingerprint ? (
              <div className="text-xs text-mist">
                当前口令指纹：
                <span className="font-mono tracking-wider text-signal">
                  {status.data.password_fingerprint}
                </span>
                <span className="ml-1 text-mist/70">（用于与访问记录比对，定位泄露口令）</span>
              </div>
            ) : null}

            <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3">
              <div>
                {hasPassword ? (
                  <Button
                    type="button"
                    variant="line"
                    disabled={pending}
                    onClick={() => {
                      if (window.confirm('清除口令并关闭门禁？公开页将恢复匿名访问。')) clear.mutate()
                    }}
                  >
                    清除口令
                  </Button>
                ) : null}
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
                  取消
                </Button>
                <Button
                  type="button"
                  disabled={pending || (enabled && !willHavePassword)}
                  onClick={() => save.mutate()}
                >
                  {save.isPending ? '保存中…' : '保存'}
                </Button>
              </div>
            </div>

            {hasPassword ? (
              <div className="border-t border-line pt-3">
                <Button type="button" variant="line" onClick={() => setSessionsOpen(true)}>
                  <History size={15} /> 查看访问记录
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>

      {sessionsOpen ? (
        <InfoPublicSessionsDialog open scope={scope} onClose={() => setSessionsOpen(false)} />
      ) : null}
    </Dialog>
  )
}

export default InfoPublicGateDialog
