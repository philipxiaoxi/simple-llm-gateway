import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type InfoAiSettings } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'
import { Button, Dialog, Field, Input, Select, Switch } from './ui'

export const INFO_AI_QUERY_KEY = ['info-ai-settings'] as const

type FormState = {
  enabled: boolean
  account_id: string
  model: string
  feature_threshold: number
  vision_max_images: number
  max_image_bytes: number
  hide_ads: boolean
  max_attempts: number
  prompt_template: string
}

function fromSettings(data: InfoAiSettings): FormState {
  return {
    enabled: data.enabled,
    account_id: data.account_id ? String(data.account_id) : '',
    model: data.model,
    feature_threshold: data.feature_threshold,
    vision_max_images: data.vision_max_images,
    max_image_bytes: data.max_image_bytes,
    hide_ads: data.hide_ads,
    max_attempts: data.max_attempts,
    prompt_template: data.prompt_template,
  }
}

export function InfoAiSettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: INFO_AI_QUERY_KEY, queryFn: api.infoAiSettings, enabled: open })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: api.accounts, enabled: open })
  const [form, setForm] = useState<FormState | null>(null)

  useEffect(() => {
    if (open && settings.data) setForm(fromSettings(settings.data))
  }, [open, settings.data])

  const accountList = accounts.data ?? []
  const selectedAccount = accountList.find((item) => String(item.id) === form?.account_id)
  const modelOptions = (selectedAccount?.models ?? []).filter((item) => item.enabled !== false)

  function patch(next: Partial<FormState>) {
    setForm((current) => (current ? { ...current, ...next } : current))
  }

  async function invalidate() {
    await queryClient.invalidateQueries({ queryKey: INFO_AI_QUERY_KEY })
    await queryClient.invalidateQueries({ queryKey: ['info-stats'] })
    await queryClient.invalidateQueries({ queryKey: ['info-items'] })
  }

  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error('表单未就绪')
      return api.infoAiSettingsUpdate({
        enabled: form.enabled,
        account_id: form.account_id ? Number(form.account_id) : null,
        model: form.model.trim(),
        feature_threshold: form.feature_threshold,
        vision_max_images: form.vision_max_images,
        max_image_bytes: form.max_image_bytes,
        hide_ads: form.hide_ads,
        max_attempts: form.max_attempts,
        prompt_template: form.prompt_template,
      })
    },
    onSuccess: async () => {
      notifyOk('AI 判定配置已保存')
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const rescore = useMutation({
    mutationFn: (scope: 'pending' | 'failed' | 'all') => api.infoAiRescore({ scope }),
    onSuccess: async (result) => {
      notifyOk(`已重置 ${result.count} 条待判定`)
      await invalidate()
    },
    onError: (caught) => notifyBad(errorMessage(caught, '重新判定失败')),
  })

  const pending = save.isPending || rescore.isPending

  return (
    <Dialog title="资讯 AI 判定" onClose={() => (pending ? undefined : onClose())}>
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium text-paper">
          <Sparkles size={16} className="text-signal" /> 广告过滤与价值打分
        </div>
        <p className="text-xs text-mist">
          新采集内容先入库，再由后台用所选账号与模型异步判定。模型支持图片时会附带条目图片。
        </p>

        {!form ? (
          <div className="text-sm text-mist">加载中…</div>
        ) : (
          <>
            <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
              <div>
                <div className="text-sm text-paper">启用 AI 判定</div>
                <div className="text-xs text-mist">关闭后停止上游调用，已有判定结果保留。</div>
              </div>
              <Switch checked={form.enabled} onCheckedChange={(value) => patch({ enabled: value })} />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="上游账号">
                <Select
                  value={form.account_id}
                  onChange={(event) => patch({ account_id: event.target.value, model: '' })}
                  disabled={pending}
                >
                  <option value="">未选择</option>
                  {accountList.map((item) => (
                    <option key={item.id} value={String(item.id)}>
                      {item.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="模型（留空则用账号首个模型）">
                <Select
                  value={form.model}
                  onChange={(event) => patch({ model: event.target.value })}
                  disabled={pending || !form.account_id}
                >
                  <option value="">默认</option>
                  {modelOptions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id}
                      {item.modalities?.input?.includes('image') ? '（视觉）' : ''}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="精选分数阈值（0-100）">
                <Input
                  type="number"
                  min={0}
                  max={100}
                  value={form.feature_threshold}
                  onChange={(event) => patch({ feature_threshold: Number(event.target.value) })}
                  disabled={pending}
                />
              </Field>
              <Field label="判定传图上限（张）">
                <Input
                  type="number"
                  min={0}
                  max={10}
                  value={form.vision_max_images}
                  onChange={(event) => patch({ vision_max_images: Number(event.target.value) })}
                  disabled={pending}
                />
              </Field>
              <Field label="失败重试上限">
                <Input
                  type="number"
                  min={1}
                  max={10}
                  value={form.max_attempts}
                  onChange={(event) => patch({ max_attempts: Number(event.target.value) })}
                  disabled={pending}
                />
              </Field>
              <div className="flex items-center justify-between gap-3 rounded-md border border-line px-3 py-2">
                <span className="text-sm text-paper">广告自动隐藏</span>
                <Switch checked={form.hide_ads} onCheckedChange={(value) => patch({ hide_ads: value })} />
              </div>
            </div>
            <Field label="自定义提示词（留空使用内置）">
              <textarea
                value={form.prompt_template}
                onChange={(event) => patch({ prompt_template: event.target.value })}
                rows={3}
                disabled={pending}
                className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none focus:border-signal/70"
              />
            </Field>
            {settings.isError ? (
              <div className="text-xs text-danger">{errorMessage(settings.error, '加载配置失败')}</div>
            ) : null}
            <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3">
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="line"
                  disabled={pending}
                  onClick={() => rescore.mutate('pending')}
                >
                  重新判定待处理
                </Button>
                <Button
                  type="button"
                  variant="line"
                  disabled={pending}
                  onClick={() => rescore.mutate('failed')}
                >
                  重试失败
                </Button>
                <Button
                  type="button"
                  variant="line"
                  disabled={pending}
                  onClick={() => {
                    if (window.confirm('重新判定全部条目？数据量大时耗时较长。')) rescore.mutate('all')
                  }}
                >
                  全部重判
                </Button>
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>
                  取消
                </Button>
                <Button type="button" disabled={pending} onClick={() => save.mutate()}>
                  {save.isPending ? '保存中…' : '保存'}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}

export default InfoAiSettingsDialog
