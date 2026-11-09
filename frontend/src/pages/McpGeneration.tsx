import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Cog, ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Button, Card, Field, Input, Switch } from '../components/ui'
import { GENERATION_SETTINGS_KEY, useGenerationSettings } from '../components/useGenerationSettings'
import { PageHeader } from '../components/layout-primitives'

type ProviderForm = {
  provider: string
  label: string
  kinds: string[]
  base_url: string
  api_key: string
  extra_models: string
  enabled: boolean
  has_key: boolean
}

const KIND_LABEL: Record<string, string> = { image: '图片', video: '视频' }

/**
 * 「图片 / 视频生成」能力的服务目录管理页。
 *
 * 这里只配置各家生成供应商的凭据。图片 / 视频模型由下游调用时通过 model 参数选择，
 * 参数与返回值可先用 image_models / video_models 工具查询。
 *
 * 供应商列表来自后端注册表（`app/generation/providers/`），新增供应商后这里会自动多出一张卡片。
 */
export function McpGenerationPage() {
  const canImage = useCan('image_gen:view')
  const canVideo = useCan('video_gen:view')
  const queryClient = useQueryClient()
  const settings = useGenerationSettings()
  const [providers, setProviders] = useState<ProviderForm[]>([])
  const [clearing, setClearing] = useState('')

  useEffect(() => {
    if (!settings.data) return
    setProviders(
      settings.data.providers.map((item) => ({
        provider: item.provider,
        label: item.label,
        kinds: item.kinds,
        base_url: item.base_url,
        api_key: '',
        extra_models: item.extra_models,
        enabled: item.enabled,
        has_key: item.has_key,
      })),
    )
  }, [settings.data])

  function patchProvider(index: number, next: Partial<ProviderForm>) {
    setProviders((current) => current.map((item, i) => (i === index ? { ...item, ...next } : item)))
  }

  const save = useMutation({
    mutationFn: () =>
      api.generationSettingsUpdate({
        providers: providers.map((item) => ({
          provider: item.provider,
          base_url: item.base_url,
          api_key: item.api_key || undefined,
          extra_models: item.extra_models,
          enabled: item.enabled,
        })),
      }),
    onSuccess: async () => {
      notifyOk('生成配置已保存')
      setProviders((current) => current.map((item) => ({ ...item, api_key: '' })))
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  async function clearKey(provider: string) {
    setClearing(provider)
    try {
      await api.generationClearKey(provider)
      notifyOk('已清除 API Key')
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    } catch (caught) {
      notifyBad(errorMessage(caught, '清除失败'))
    } finally {
      setClearing('')
    }
  }

  const pending = save.isPending || Boolean(clearing)

  return (
    <div className="space-y-4">
      <PageHeader
        title="图片 / 视频生成"
        description="配置生成供应商凭据。模型由下游调用时通过 model 参数选择，可先用 image_models / video_models 工具查询参数与返回值。"
      />

      {!settings.data ? (
        <Card>
          <div className="text-sm text-mist">
            {settings.isError ? errorMessage(settings.error, '加载配置失败') : '加载中…'}
          </div>
        </Card>
      ) : (
        <>
          {providers.map((item, index) => (
            <Card key={item.provider} className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-medium text-paper">
                  <Cog size={16} className="text-signal" /> {item.label}
                  <span className="text-xs font-normal text-mist">
                    支持 {item.kinds.map((kind) => KIND_LABEL[kind] ?? kind).join(' / ') || '—'}
                  </span>
                </div>
                <Switch
                  checked={item.enabled}
                  onCheckedChange={(value) => patchProvider(index, { enabled: value })}
                  disabled={pending}
                />
              </div>
              <Field label="Base URL">
                <Input
                  value={item.base_url}
                  onChange={(event) => patchProvider(index, { base_url: event.target.value })}
                  placeholder={settings.data.providers[index]?.default_base_url}
                  disabled={pending}
                />
              </Field>
              <Field label={item.has_key ? 'API Key（已配置，留空则不修改）' : 'API Key'}>
                <Input
                  type="password"
                  value={item.api_key}
                  onChange={(event) => patchProvider(index, { api_key: event.target.value })}
                  placeholder={item.has_key ? '••••••••' : 'sk-...'}
                  autoComplete="off"
                  disabled={pending}
                />
              </Field>
              <Field label="自定义模型（可选）">
                <Input
                  value={item.extra_models}
                  onChange={(event) => patchProvider(index, { extra_models: event.target.value })}
                  placeholder="逗号或空格分隔，例如 nano-banana-2, gpt-image-2"
                  disabled={pending}
                />
              </Field>
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-mist">
                  供应商未公开模型列表时（如 RightAPI 只在自家后台可见），把模型名填在这里即可直接使用。同时支持图片和视频的供应商（如 gsai），用 模型名:video 指定为视频模型。
                </span>
                {item.has_key ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-danger"
                    disabled={pending}
                    onClick={() => void clearKey(item.provider)}
                  >
                    {clearing === item.provider ? '清除中…' : '清除 Key'}
                  </Button>
                ) : null}
              </div>
            </Card>
          ))}

          <Card className="space-y-3">
            <p className="text-xs text-mist">
              模型由下游调用时通过 model 参数选择（provider:model 或模型名）；不传则使用 Key 配置或服务端默认模型。
              调用前可先用 image_models / video_models 工具查询每个模型的参数与返回值。
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
              <div className="flex gap-2 text-sm">
                {canImage ? (
                <Link to="/image-gen" className="inline-flex items-center gap-1 text-signal hover:brightness-110">
                  图片生成页 <ExternalLink size={13} />
                </Link>
                ) : null}
                {canVideo ? (
                <Link to="/video-gen" className="inline-flex items-center gap-1 text-signal hover:brightness-110">
                  视频生成页 <ExternalLink size={13} />
                </Link>
                ) : null}
              </div>
              <Button type="button" disabled={pending} onClick={() => save.mutate()}>
                {save.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

export default McpGenerationPage
