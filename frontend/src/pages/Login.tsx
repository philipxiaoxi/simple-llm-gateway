import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Field, Input } from '../components/ui'
import { api, setToken } from '../lib/api'
import { errorMessage } from '../lib/utils'

export function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const result = await api.login(username, password)
      setToken(result.token)
      navigate('/')
    } catch (err) {
      setError(errorMessage(err, '登录失败'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <div className="font-mono text-xs tracking-[0.28em] text-signal">SIGNAL DESK</div>
        <h1 className="mt-2 text-2xl font-semibold">登录中转台</h1>
        <p className="mt-1 text-sm text-mist">管理员控制台，下游客户端请用 API Key 直连网关。</p>
        <form className="mt-6 space-y-4" onSubmit={onSubmit}>
          <Field label="用户名">
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Field label="密码">
            <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </Field>
          {error ? <div className="text-sm text-danger">{error}</div> : null}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? '登录中…' : '进入控制台'}
          </Button>
        </form>
      </Card>
    </div>
  )
}
