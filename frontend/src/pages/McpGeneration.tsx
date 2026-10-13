import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Cog, ExternalLink, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { resolveModelKey } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { GenerationModelSelect } from '../components/GenerationModelSelect'
import { Button, Card, Field, Input, Switch } from '../components/ui'
import { GENERATION_SETTINGS_KEY, useGenerationSettings } from '../components/useGenerationSettings'

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
 * 这里配置两件事：各家生成供应商的凭据，以及该能力对外提供的图片 / 视频模型 —— 后者会作为
 * MCP / REST 调用固定使用的模型（下游只能传参数、不能切换模型）。
 *
 * 供应商列表来自后端注册表（`app/generation/providers/`），新增供应商后这里会自动多出一张卡片。
 */
export function McpGenerationPage() {
  const queryClient = useQueryClient()
  const settings = useGenerationSettings()
  const [providers, setProviders] = useState<ProviderForm[]>([])
  const [imageModel, setImageModel] = useState('')
  const [videoModel, setVideoModel] = useState('')
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
    setImageModel(resolveModelKey(settings.data.catalog.image, settings.data.default_image_model))
    setVideoModel(resolveModelKey(settings.data.catalog.video, settings.data.default_video_model))
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
        default_image_model: imageModel,
        default_video_model: videoModel,
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
      <div>
        <h1 className="text-xl font-semibold">图片 / 视频生成</h1>
        <p className="mt-1 text-sm text-mist">
          配置生成供应商凭据，并选定该能力对外提供的模型。MCP / REST 调用固定使用所选模型，只能传提示词与参数，不能切换模型。
        </p>
      </div>

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
            <div className="flex items-center gap-2 text-sm font-medium text-paper">
              <Sparkles size={16} className="text-signal" /> 对外提供的模型
            </div>
            <p className="text-xs text-mist">
              下游调用只能配置参数（比例 / 尺寸 / 时长等），无法切换模型。
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="图片模型">
                <GenerationModelSelect
                  models={settings.data.catalog.image}
                  value={imageModel}
                  onChange={setImageModel}
                  disabled={pending}
                />
              </Field>
              <Field label="视频模型">
                <GenerationModelSelect
                  models={settings.data.catalog.video}
                  value={videoModel}
                  onChange={setVideoModel}
                  disabled={pending}
                />
              </Field>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
              <div className="flex gap-2 text-sm">
                <Link to="/image-gen" className="inline-flex items-center gap-1 text-signal hover:brightness-110">
                  图片生成页 <ExternalLink size={13} />
                </Link>
                <Link to="/video-gen" className="inline-flex items-center gap-1 text-signal hover:brightness-110">
                  视频生成页 <ExternalLink size={13} />
                </Link>
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
