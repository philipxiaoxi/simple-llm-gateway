import { useMutation } from '@tanstack/react-query'
import { ImagePlus, Music, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type GenerationAsset, type GenerationModel, type GenerationSettings } from '../lib/api'
import { defaultParams } from '../lib/generation'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Button, Field, Input, Select } from './ui'

type ParamValue = string

function initialParams(model: GenerationModel | undefined): Record<string, ParamValue> {
  if (!model) return {}
  const raw = defaultParams(model)
  const result: Record<string, ParamValue> = {}
  for (const [key, value] of Object.entries(raw)) result[key] = String(value ?? '')
  return result
}

function buildParams(model: GenerationModel, values: Record<string, ParamValue>) {
  const result: Record<string, unknown> = {}
  for (const param of model.params) {
    const value = values[param.key] ?? ''
    if (value === '') continue
    result[param.key] = param.type === 'number' ? Number(value) : value
  }
  return result
}

export function GenerationComposer({
  kind,
  settings,
  onCreated,
  onOpenConfig,
}: {
  kind: 'image' | 'video'
  settings: GenerationSettings
  onCreated: () => void
  onOpenConfig: () => void
}) {
  const models = settings.catalog[kind]
  const defaultModel = kind === 'image' ? settings.default_image_model : settings.default_video_model
  const [modelId, setModelId] = useState(defaultModel || models[0]?.id || '')
  const [prompt, setPrompt] = useState('')
  const [params, setParams] = useState<Record<string, ParamValue>>({})
  const [inputs, setInputs] = useState<GenerationAsset[]>([])
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const model = models.find((item) => item.id === modelId) ?? models[0]

  useEffect(() => {
    setParams(initialParams(model))
  }, [model])

  const configured = settings.providers.some((item) => item.configured && item.enabled)
  const create = useMutation({
    mutationFn: () =>
      api.generationCreate({
        kind,
        model: modelId,
        prompt: prompt.trim(),
        params: buildParams(model, params),
        input_asset_ids: inputs.map((asset) => asset.id),
      }),
    onSuccess: () => {
      notifyOk('已提交生成任务')
      setPrompt('')
      setInputs([])
      onCreated()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '提交失败')),
  })

  async function handleFiles(files: FileList | null) {
    if (!files || !model) return
    const imageLimit = model.supports_images ? model.max_images : 0
    const audioLimit = model.supports_audios ? model.max_audios : 0
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const isAudio = file.type.startsWith('audio/')
        if (isAudio && !model.supports_audios) {
          notifyBad('该模型不支持参考音频')
          continue
        }
        if (!isAudio && !model.supports_images) {
          notifyBad('该模型不支持参考图片')
          continue
        }
        const imageCount = inputs.filter((item) => item.kind !== 'audio').length
        const audioCount = inputs.filter((item) => item.kind === 'audio').length
        if (isAudio && audioCount >= audioLimit) {
          notifyBad(`参考音频最多 ${audioLimit} 个`)
          break
        }
        if (!isAudio && imageCount >= imageLimit) {
          notifyBad(`参考图片最多 ${imageLimit} 张`)
          break
        }
        try {
          const asset = await api.generationUpload(file)
          setInputs((current) => [...current, asset])
        } catch (caught) {
          notifyBad(errorMessage(caught, `${file.name} 上传失败`))
        }
      }
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function removeInput(asset: GenerationAsset) {
    setInputs((current) => current.filter((item) => item.id !== asset.id))
    try {
      await api.generationDeleteUpload(asset.id)
    } catch {
      /* 删除失败不影响使用 */
    }
  }

  if (!model) {
    return <div className="text-sm text-mist">暂无可用的{kind === 'image' ? '图片' : '视频'}模型。</div>
  }

  return (
    <div className="space-y-3">
      {!configured ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          <span>尚未配置生成供应商，提交前请先完成配置。</span>
          <Button type="button" variant="line" onClick={onOpenConfig}>
            去配置
          </Button>
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
        <div className="space-y-3">
          <Field label="提示词">
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={5}
              placeholder={kind === 'image' ? '描述你想生成的画面，例如：一只边牧在直播间带货' : '描述镜头、动作、节奏与声音，例如：深夜未来都市，暴雨，电影质感'}
              className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none focus:border-signal/70"
            />
          </Field>

          {(model.supports_images || model.supports_audios) && (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-[0.16em] text-mist">参考素材</div>
              <div className="flex flex-wrap gap-2">
                {inputs.map((asset) => (
                  <div key={asset.id} className="relative">
                    {asset.kind === 'audio' ? (
                      <div className="flex h-16 w-16 flex-col items-center justify-center rounded-md border border-line bg-panel-2 text-[10px] text-mist">
                        <Music size={16} />
                        音频
                      </div>
                    ) : (
                      <img
                        src={asset.url}
                        alt="参考图"
                        className="h-16 w-16 rounded-md border border-line object-cover"
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => void removeInput(asset)}
                      className="absolute -right-1.5 -top-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-danger text-white"
                      aria-label="移除"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
                <label className="flex h-16 w-16 cursor-pointer flex-col items-center justify-center rounded-md border border-dashed border-line text-[10px] text-mist hover:border-signal/60 hover:text-paper">
                  <ImagePlus size={16} />
                  {uploading ? '上传中' : '添加'}
                  <input
                    ref={fileRef}
                    type="file"
                    multiple
                    accept={
                      model.supports_images && model.supports_audios
                        ? 'image/*,audio/*'
                        : model.supports_audios
                          ? 'audio/*'
                          : 'image/*'
                    }
                    className="hidden"
                    onChange={(event) => void handleFiles(event.target.files)}
                  />
                </label>
              </div>
              <div className="text-[11px] text-mist">
                支持本地上传
                {model.supports_images ? `，参考图最多 ${model.max_images} 张` : ''}
                {model.supports_audios ? `，参考音频最多 ${model.max_audios} 个` : ''}
              </div>
            </div>
          )}
        </div>

        <div className="space-y-3">
          <Field label="模型">
            <Select value={modelId} onChange={(event) => setModelId(event.target.value)}>
              {models.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-2">
            {model.params.map((param) => (
              <div key={param.key} className={param.type === 'number' ? '' : 'col-span-2'}>
                <Field label={param.required ? `${param.label} *` : param.label}>
                  {param.type === 'select' ? (
                    <Select
                      value={params[param.key] ?? ''}
                      onChange={(event) => setParams((current) => ({ ...current, [param.key]: event.target.value }))}
                    >
                      {param.options.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </Select>
                  ) : param.type === 'combo' ? (
                    <>
                      <Input
                        list={`gen-${param.key}`}
                        value={params[param.key] ?? ''}
                        onChange={(event) => setParams((current) => ({ ...current, [param.key]: event.target.value }))}
                      />
                      <datalist id={`gen-${param.key}`}>
                        {param.options.map((option) => (
                          <option key={option.value} value={option.value} />
                        ))}
                      </datalist>
                    </>
                  ) : (
                    <Input
                      type="number"
                      min={param.min ?? undefined}
                      max={param.max ?? undefined}
                      value={params[param.key] ?? ''}
                      onChange={(event) => setParams((current) => ({ ...current, [param.key]: event.target.value }))}
                    />
                  )}
                </Field>
                {param.help ? <div className="mt-1 text-[11px] text-mist">{param.help}</div> : null}
              </div>
            ))}
          </div>

          <Button
            type="button"
            className="w-full"
            disabled={create.isPending || uploading || !prompt.trim()}
            onClick={() => create.mutate()}
          >
            <Sparkles size={16} />
            {create.isPending ? '提交中…' : kind === 'image' ? '生成图片' : '生成视频'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default GenerationComposer
