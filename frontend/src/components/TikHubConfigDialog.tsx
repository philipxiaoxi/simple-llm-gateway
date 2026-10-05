import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'
import { Badge, Button, Dialog, Field, Input } from './ui'

export const TIKHUB_QUERY_KEY = ['tikhub-config'] as const

function sourceLabel(source: string) {
  if (source === 'page') return '管理页'
  if (source === 'env') return '环境变量'
  return source || '未配置'
}

export function TikHubConfigDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const provider = useQuery({ queryKey: TIKHUB_QUERY_KEY, queryFn: api.tikhubStatus, enabled: open })
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    if (open) setBaseUrl(provider.data?.base_url || '')
  }, [open, provider.data])

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: TIKHUB_QUERY_KEY })
  }

  const saveProvider = useMutation({
    mutationFn: () =>
      api.saveTikhub({
        base_url: baseUrl.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }),
    onSuccess: async () => {
      notifyOk('TikHub 配置已保存')
      setApiKey('')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const clearProvider = useMutation({
    mutationFn: () => api.clearTikhub(),
    onSuccess: async () => {
      notifyOk('TikHub 配置已清除')
      setApiKey('')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '清除失败')),
  })

  const pending = saveProvider.isPending || clearProvider.isPending

  return (
    <Dialog title="TikHub 凭据" onClose={() => (pending ? undefined : onClose())}>
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-paper">
          <Cloud size={16} className="text-signal" /> TikHub 解析 API
        </div>
        <p className="text-xs text-mist">
          抖音下载与资讯采集共用同一份 TikHub 凭据，配置一次两处生效。Key
          加密存储、不回显；清除后回退环境变量。
        </p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-mist">
          {provider.data?.configured ? <Badge tone="ok">已配置</Badge> : <Badge tone="warn">未配置</Badge>}
          {provider.data?.source ? <span>来源：{sourceLabel(provider.data.source)}</span> : null}
          {provider.data?.updated_at ? <span>更新：{formatTime(provider.data.updated_at)}</span> : null}
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Base URL">
            <Input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.tikhub.io"
              disabled={pending}
            />
          </Field>
          <Field label="API Key（留空则不修改）">
            <Input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={provider.data?.has_key ? '已配置' : 'tk_...'}
              disabled={pending}
            />
          </Field>
        </div>
        {provider.isError ? (
          <div className="text-xs text-danger">{errorMessage(provider.error, '加载凭据状态失败')}</div>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          {provider.data?.source === 'page' ? (
            <Button
              type="button"
              variant="danger"
              disabled={pending}
              onClick={() => {
                if (window.confirm('清除 TikHub 配置？清除后将回退环境变量。')) clearProvider.mutate()
              }}
            >
              <Trash2 size={14} /> 清除
            </Button>
          ) : null}
          <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button
            type="button"
            disabled={pending || !baseUrl.trim()}
            onClick={() => saveProvider.mutate()}
          >
            {saveProvider.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export default TikHubConfigDialog
