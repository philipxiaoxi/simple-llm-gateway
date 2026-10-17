import { useMutation } from '@tanstack/react-query'
import { Dices, ImagePlus, Music, Sparkles, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type GenerationAsset, type GenerationModel, type GenerationSettings } from '../lib/api'
import type { GenerationPreset } from '../lib/generation'
import {
  defaultParams,
  findModel,
  modelKey,
  pickExample,
  resolveModelKey,
  resolvePresetTarget,
} from '../lib/generation'
import { GenerationModelSelect } from './GenerationModelSelect'
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
  preset,
  onCreated,
  onOpenConfig,
}: {
  kind: 'image' | 'video'
  settings: GenerationSettings
  preset?: GenerationPreset | null
  onCreated: () => void
  onOpenConfig: () => void
}) {
  const models = settings.catalog[kind]
  const defaultModel = kind === 'image' ? settings.default_image_model : settings.default_video_model
  const [targetKey, setTargetKey] = useState(() => resolveModelKey(models, defaultModel))
  const [prompt, setPrompt] = useState('')
  const [params, setParams] = useState<Record<string, ParamValue>>(() =>
    initialParams(findModel(models, resolveModelKey(models, defaultModel))),
  )
  const [inputs, setInputs] = useState<GenerationAsset[]>([])
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const model = findModel(models, targetKey) ?? models[0]
  // 目录里有多家供应商时，只认「当前模型所属那家」是否可用
  const providerSetting = settings.providers.find((item) => item.provider === model?.provider)
  const configured = Boolean(providerSetting?.configured && providerSetting.enabled)

  // 复用某个作品的参数：一次性填充模型/提示词/参数（不改动已上传的参考素材）
  useEffect(() => {
    if (!preset) return
    // 以作品自己的供应商为准，避免同名模型被填成另一家
    const target = resolvePresetTarget(models, preset, model?.provider)
    if (!target) return
    const merged = initialParams(target)
    for (const param of target.params) {
      if (param.key in preset.params) merged[param.key] = String(preset.params[param.key] ?? '')
    }
    setTargetKey(modelKey(target))
    setPrompt(preset.prompt)
    setParams(merged)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.nonce])

  const create = useMutation({
    mutationFn: () =>
      api.generationCreate({
        kind,
        provider: model.provider,
        model: model.id,
        prompt: prompt.trim(),
        params: buildParams(model, params),
        input_asset_ids: inputs.map((asset) => asset.id),
      }),
    onSuccess: () => {
      notifyOk('已提交生成任务')
      // 保留提示词方便微调重试；参考素材已随任务提交，清空以免重复携带
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

  function changeModel(nextKey: string) {
    setTargetKey(nextKey)
    setParams(initialParams(findModel(models, nextKey)))
  }

  function submit() {
    if (create.isPending || uploading || !prompt.trim()) return
    create.mutate()
  }

  if (!model) {
    return <div className="text-sm text-mist">暂无可用的{kind === 'image' ? '图片' : '视频'}模型。</div>
  }

  const accept = model.supports_images && model.supports_audios ? 'image/*,audio/*' : model.supports_audios ? 'audio/*' : 'image/*'
  const supportsRefs = model.supports_images || model.supports_audios

  return (
    <div className="flex flex-col gap-4">
      {!configured ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          <span>
            {model.provider_label || model.provider} 尚未配置，提交前请先完成配置。
          </span>
          <Button type="button" variant="line" className="min-h-8 py-1" onClick={onOpenConfig}>
            去配置
          </Button>
        </div>
      ) : null}

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-[0.16em] text-mist">提示词</span>
          <button
            type="button"
            onClick={() => setPrompt(pickExample(kind))}
            className="inline-flex items-center gap-1 text-xs text-mist transition hover:text-signal"
          >
            <Dices size={13} /> 示例
          </button>
        </div>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              submit()
            }
          }}
          rows={6}
          placeholder={
            kind === 'image'
              ? '描述你想生成的画面：主体、场景、光线、风格……'
              : '描述镜头、动作、节奏与声音：主体、运镜、时长、氛围……'
          }
          className="w-full resize-y rounded-lg border border-line-strong bg-control px-3 py-2.5 text-sm leading-6 text-paper outline-none focus:border-signal focus:ring-2 focus:ring-signal/20"
        />
        <div className="text-right text-xs text-mist">Ctrl / ⌘ + Enter 生成</div>
      </div>

      {supportsRefs ? (
        <div className="space-y-2">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">参考素材</div>
          <div className="flex flex-wrap gap-2">
            {inputs.map((asset) => (
              <div key={asset.id} className="group relative">
                {asset.kind === 'audio' ? (
                  <div className="flex h-16 w-16 flex-col items-center justify-center gap-1 rounded-lg border border-line bg-panel-2 text-[10px] text-mist">
                    <Music size={16} />
                    音频
                  </div>
                ) : (
                  <img src={asset.thumb_url ?? asset.url} alt="参考图" className="h-16 w-16 rounded-lg border border-line object-cover" />
                )}
                <button
                  type="button"
                  onClick={() => void removeInput(asset)}
                  className="absolute -right-1.5 -top-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-danger text-white opacity-0 transition group-hover:opacity-100"
                  aria-label="移除"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            <label className="flex h-16 w-16 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-line text-[10px] text-mist transition hover:border-signal/60 hover:text-paper">
              <ImagePlus size={16} />
              {uploading ? '上传中' : '添加'}
              <input ref={fileRef} type="file" multiple accept={accept} className="hidden" onChange={(event) => void handleFiles(event.target.files)} />
            </label>
          </div>
          <div className="text-[11px] text-mist">
            仅支持本地上传
            {model.supports_images ? `，参考图最多 ${model.max_images} 张` : ''}
            {model.supports_audios ? `，参考音频最多 ${model.max_audios} 个` : ''}
          </div>
        </div>
      ) : null}

      <Field label="模型">
        <GenerationModelSelect models={models} value={targetKey} onChange={changeModel} />
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

      <div className="flex gap-2">
        <Button
          type="button"
          variant="line"
          className="px-3"
          title="清空"
          disabled={create.isPending || !prompt}
          onClick={() => {
            setPrompt('')
            setInputs([])
          }}
        >
          <Trash2 size={15} />
        </Button>
        <Button type="button" className="flex-1" disabled={create.isPending || uploading || !prompt.trim()} onClick={submit}>
          <Sparkles size={16} />
          {create.isPending ? '提交中…' : kind === 'image' ? '生成图片' : '生成视频'}
        </Button>
      </div>
    </div>
  )
}

export default GenerationComposer
