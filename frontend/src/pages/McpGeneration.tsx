import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Cog, ExternalLink, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Button, Card, Field, Input, Select, Switch } from '../components/ui'
import { GENERATION_SETTINGS_KEY, useGenerationSettings } from '../components/useGenerationSettings'

/**
 * 「图片 / 视频生成」能力的服务目录管理页。
 *
 * 这里配置两件事：生成供应商的凭据，以及该能力对外提供的图片 / 视频模型 —— 后者会作为
 * MCP / REST 调用固定使用的模型（下游只能传参数、不能切换模型）。
 */
export function McpGenerationPage() {
  const queryClient = useQueryClient()
  const settings = useGenerationSettings()
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [hasKey, setHasKey] = useState(false)
  const [imageModel, setImageModel] = useState('')
  const [videoModel, setVideoModel] = useState('')

  useEffect(() => {
    if (!settings.data) return
    const provider = settings.data.providers.find((item) => item.provider === 'gsai')
    setBaseUrl(provider?.base_url ?? '')
    setEnabled(provider?.enabled ?? true)
    setHasKey(provider?.has_key ?? false)
    setImageModel(settings.data.default_image_model)
    setVideoModel(settings.data.default_video_model)
  }, [settings.data])

  const save = useMutation({
    mutationFn: () =>
      api.generationSettingsUpdate({
        providers: [{ provider: 'gsai', base_url: baseUrl, api_key: apiKey || undefined, enabled }],
        default_image_model: imageModel,
        default_video_model: videoModel,
      }),
    onSuccess: async () => {
      notifyOk('生成配置已保存')
      setApiKey('')
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const clearKey = useMutation({
    mutationFn: () => api.generationClearKey('gsai'),
    onSuccess: async () => {
      notifyOk('已清除 API Key')
      await queryClient.invalidateQueries({ queryKey: GENERATION_SETTINGS_KEY })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '清除失败')),
  })

  const pending = save.isPending || clearKey.isPending

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
          <Card className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-paper">
              <Cog size={16} className="text-signal" /> 生成供应商（gsai）
            </div>
            <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
              <div className="text-sm text-paper">启用</div>
              <Switch checked={enabled} onCheckedChange={setEnabled} disabled={pending} />
            </div>
            <Field label="Base URL">
              <Input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://grsaiapi.com"
                disabled={pending}
              />
            </Field>
            <Field label={hasKey ? 'API Key（已配置，留空则不修改）' : 'API Key'}>
              <Input
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={hasKey ? '••••••••' : 'sk-...'}
                autoComplete="off"
                disabled={pending}
              />
            </Field>
            {hasKey ? (
              <div className="flex justify-end">
                <Button type="button" variant="ghost" className="text-danger" disabled={pending} onClick={() => clearKey.mutate()}>
                  清除 Key
                </Button>
              </div>
            ) : null}
          </Card>

          <Card className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-paper">
              <Sparkles size={16} className="text-signal" /> 对外提供的模型
            </div>
            <p className="text-xs text-mist">
              下游调用只能配置参数（比例 / 尺寸 / 时长等），无法切换模型。
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="图片模型">
                <Select value={imageModel} onChange={(event) => setImageModel(event.target.value)} disabled={pending}>
                  {settings.data.catalog.image.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="视频模型">
                <Select value={videoModel} onChange={(event) => setVideoModel(event.target.value)} disabled={pending}>
                  {settings.data.catalog.video.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </Select>
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
