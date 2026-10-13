import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Cog } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type GenerationSettings } from '../lib/api'
import { resolveModelKey } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { GenerationModelSelect } from './GenerationModelSelect'
import { Button, Dialog, Field, Input, Switch } from './ui'
import { GENERATION_SETTINGS_KEY, useGenerationSettings } from './useGenerationSettings'

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

export function GenerationConfigDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const settings = useGenerationSettings(open)
  const [providers, setProviders] = useState<ProviderForm[]>([])
  const [imageModel, setImageModel] = useState('')
  const [videoModel, setVideoModel] = useState('')

  useEffect(() => {
    if (!open || !settings.data) return
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
    // 目录里模型带 provider，默认值统一用 provider:model 的键
    setImageModel(resolveModelKey(settings.data.catalog.image, settings.data.default_image_model))
    setVideoModel(resolveModelKey(settings.data.catalog.video, settings.data.default_video_model))
  }, [open, settings.data])

  const data: GenerationSettings | undefined = settings.data

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
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const clearKey = useMutation({
    mutationFn: (provider: string) => api.generationClearKey(provider),
    onSuccess: async () => {
      notifyOk('已清除 API Key')
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '清除失败')),
  })

  const pending = save.isPending || clearKey.isPending

  return (
    <Dialog title="生成配置" onClose={() => (pending ? undefined : onClose())}>
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-medium text-paper">
          <Cog size={16} className="text-signal" /> 供应商与默认模型
        </div>
        <p className="text-xs text-mist">
          配置生成供应商的地址与 API Key，并设置图片 / 视频默认使用的模型。API Key 加密保存，仅用于服务端调用。
        </p>

        {!data ? (
          <div className="text-sm text-mist">{settings.isError ? errorMessage(settings.error, '加载失败') : '加载中…'}</div>
        ) : (
          <>
            {providers.map((item, index) => (
              <div key={item.provider} className="space-y-2 rounded-lg border border-line p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm text-paper">
                    {item.label}
                    <span className="ml-2 text-xs text-mist">
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
                    placeholder={data.providers[index]?.default_base_url}
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
                <p className="text-[11px] text-mist">
                  供应商未公开模型列表时（如 RightAPI 只在自家后台可见），把模型名填在这里即可直接使用，无需改代码。同时支持图片和视频的供应商（如 gsai），用 模型名:video 指定为视频模型，不写则按图片处理。
                </p>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-mist">
                    {item.has_key ? '已保存 Key' : '尚未配置 Key'}
                  </span>
                  {item.has_key ? (
                    <Button
                      type="button"
                      variant="ghost"
                      className="text-danger"
                      disabled={pending}
                      onClick={() => clearKey.mutate(item.provider)}
                    >
                      清除 Key
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}

            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="默认图片模型">
                <GenerationModelSelect
                  models={data.catalog.image}
                  value={imageModel}
                  onChange={setImageModel}
                  disabled={pending}
                />
              </Field>
              <Field label="默认视频模型">
                <GenerationModelSelect
                  models={data.catalog.video}
                  value={videoModel}
                  onChange={setVideoModel}
                  disabled={pending}
                />
              </Field>
            </div>

            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
                取消
              </Button>
              <Button type="button" disabled={pending} onClick={() => save.mutate()}>
                {save.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}

export default GenerationConfigDialog
