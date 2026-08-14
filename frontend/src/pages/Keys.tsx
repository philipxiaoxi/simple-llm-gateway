import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Badge, Button, Card, Field, Input } from '../components/ui'
import { api } from '../lib/api'
import { formatTime } from '../lib/utils'

export function KeysPage() {
  const queryClient = useQueryClient()
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const { data: keys = [] } = useQuery({ queryKey: ['keys'], queryFn: api.keys })
  const [name, setName] = useState('')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [revealed, setRevealed] = useState<Record<number, string>>({})
  const [message, setMessage] = useState('')

  const createMutation = useMutation({
    mutationFn: () => api.createKey({ name, account_id: Number(accountId) }),
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ['keys'] })
      if (item.key) setRevealed((current) => ({ ...current, [item.id]: item.key as string }))
      setName('')
      setMessage('已创建，完整 Key 显示在对应卡片上，请立刻复制。')
    },
    onError: (error: Error) => setMessage(error.message),
  })

  async function showKey(id: number) {
    const item = await api.key(id)
    if (item.key) setRevealed((current) => ({ ...current, [id]: item.key as string }))
  }

  async function copy(text: string) {
    await navigator.clipboard.writeText(text)
    setMessage('已复制')
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">API Key</h1>
        <p className="mt-1 text-sm text-mist">创建时必须绑死一个上游账号。后台随时可以再看完整 sk-…</p>
      </div>
      <Card className="grid gap-3 md:grid-cols-3">
        <Field label="备注">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="给同事 A" />
        </Field>
        <Field label="绑定上游">
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value ? Number(event.target.value) : '')}
          >
            <option value="">选择账号</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.provider})
              </option>
            ))}
          </select>
        </Field>
        <div className="flex items-end">
          <Button disabled={!name || !accountId || createMutation.isPending} onClick={() => createMutation.mutate()}>
            生成 Key
          </Button>
        </div>
      </Card>
      {message ? <div className="text-sm text-info">{message}</div> : null}
      <div className="grid gap-3">
        {keys.map((item) => {
          const full = revealed[item.id]
          return (
            <Card key={item.id} className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-lg font-medium">{item.name}</div>
                  <div className="font-mono text-xs text-mist">
                    {item.key_prefix} · {item.account_name} · {item.provider}
                  </div>
                </div>
                <Badge tone={item.status === 'active' ? 'ok' : 'mist'}>{item.status}</Badge>
              </div>
              <div className="text-sm text-mist">最近使用：{formatTime(item.last_used_at)}</div>
              {full ? (
                <div className="flex flex-wrap items-center gap-2 rounded-md bg-ink p-3 font-mono text-xs break-all">
                  {full}
                  <Button variant="line" onClick={() => copy(full)}>
                    复制
                  </Button>
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button variant="line" onClick={() => showKey(item.id)}>
                  显示完整 Key
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    await api.updateKey(item.id, { status: item.status === 'active' ? 'disabled' : 'active' })
                    queryClient.invalidateQueries({ queryKey: ['keys'] })
                  }}
                >
                  {item.status === 'active' ? '停用' : '启用'}
                </Button>
                <Button
                  variant="danger"
                  onClick={async () => {
                    await api.deleteKey(item.id)
                    queryClient.invalidateQueries({ queryKey: ['keys'] })
                  }}
                >
                  删除
                </Button>
              </div>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
