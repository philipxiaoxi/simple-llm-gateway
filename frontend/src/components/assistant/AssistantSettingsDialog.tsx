import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type AssistantSettings } from '../../lib/api'
import { notifyBad, notifyOk } from '../../lib/toast'
import { Button, Dialog, Field, Input, Select, Switch } from '../ui'

type FormState = {
  enabled: boolean
  accountId: string
  model: string
  systemPrompt: string
  temperature: number
  maxTokens: number
  maxToolRounds: number
  historyLimit: number
}

function formFrom(settings: AssistantSettings): FormState {
  return {
    enabled: settings.enabled,
    accountId: settings.account_id ? String(settings.account_id) : '',
    model: settings.model,
    systemPrompt: settings.system_prompt,
    temperature: settings.temperature,
    maxTokens: settings.max_tokens,
    maxToolRounds: settings.max_tool_rounds,
    historyLimit: settings.history_message_limit,
  }
}

export function AssistantSettingsDialog({
  settings,
  onClose,
  onSaved,
}: {
  settings: AssistantSettings
  onClose: () => void
  onSaved: (settings: AssistantSettings) => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormState>(() => formFrom(settings))
  const [pending, setPending] = useState(false)
  const modelsQuery = useQuery({
    queryKey: ['assistant', 'models'],
    queryFn: api.assistantModels,
    staleTime: 60_000,
    retry: false,
  })
  const accounts = modelsQuery.data?.items ?? []
  const selectedAccount = accounts.find((account) => String(account.id) === form.accountId)
  const modelOptions = useMemo(() => {
    const values = new Set<string>()
    if (form.model) values.add(form.model)
    for (const model of selectedAccount?.models ?? []) values.add(model)
    return [...values]
  }, [form.model, selectedAccount?.models])

  useEffect(() => {
    setForm(formFrom(settings))
  }, [settings])

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
  }

  async function save() {
    setPending(true)
    try {
      const saved = await api.saveAssistantSettings({
        enabled: form.enabled,
        account_id: form.accountId ? Number(form.accountId) : null,
        model: form.model.trim(),
        system_prompt: form.systemPrompt,
        temperature: form.temperature,
        max_tokens: form.maxTokens,
        max_tool_rounds: form.maxToolRounds,
        history_message_limit: form.historyLimit,
      })
      queryClient.setQueryData(['assistant', 'settings'], saved)
      onSaved(saved)
      notifyOk('助手设置已保存')
      onClose()
    } catch (caught) {
      notifyBad(caught instanceof Error ? caught.message : '保存失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog
      title="AI 助手设置"
      onClose={onClose}
      className="max-w-2xl"
      footer={
        <div className="flex items-center justify-between gap-3">
          <Link to="/accounts" className="text-xs text-mist transition-colors hover:text-signal">
            管理上游账号与模型
          </Link>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button type="button" onClick={save} disabled={pending}>
              {pending ? <Loader2 size={14} className="animate-spin" /> : null}
              保存设置
            </Button>
          </div>
        </div>
      }
    >
      <div className="grid gap-4">
        <div className="flex items-center justify-between gap-3 rounded-xl border border-line bg-panel-2/60 px-3 py-2.5">
          <div className="min-w-0">
            <div className="text-sm font-medium text-paper">启用站内助手</div>
            <div className="mt-0.5 text-xs text-mist">关闭后右下角入口仍会保留，对话会提示助手已停用。</div>
          </div>
          <Switch checked={form.enabled} onCheckedChange={(value) => update('enabled', value)} />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <Field label="上游账号">
            <Select
              value={form.accountId}
              onChange={(event) => {
                const accountId = event.target.value
                const account = accounts.find((item) => String(item.id) === accountId)
                setForm((current) => ({
                  ...current,
                  accountId,
                  model: account?.models[0] ?? '',
                }))
              }}
            >
              <option value="">选择账号</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                  {account.status !== 'active' ? '（已停用）' : ''}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="对话模型">
            <Select
              value={form.model}
              onChange={(event) => update('model', event.target.value)}
              disabled={!form.accountId || !modelOptions.length}
            >
              <option value="">{form.accountId ? '选择模型' : '先选择上游账号'}</option>
              {modelOptions.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {modelsQuery.isSuccess && !accounts.length ? (
          <div className="rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs leading-5 text-warn">
            还没有可用的上游账号。先到「上游账号」新增并刷新模型，再回来配置助手。
          </div>
        ) : null}
        {selectedAccount && !selectedAccount.models.length ? (
          <div className="flex items-center gap-2 rounded-lg border border-line bg-elevate px-3 py-2 text-xs text-mist">
            <RefreshCw size={13} />
            这个账号还没有模型目录，请先到上游账号页刷新模型。
          </div>
        ) : null}

        <Field label="系统提示词">
          <textarea
            value={form.systemPrompt}
            onChange={(event) => update('systemPrompt', event.target.value)}
            className="min-h-28 w-full resize-y rounded-md border border-input bg-control px-3 py-2 text-sm leading-6 text-paper outline-none transition-[border-color,box-shadow] placeholder:text-mist-soft focus:border-signal focus:ring-2 focus:ring-signal/20"
            maxLength={8000}
            spellCheck={false}
          />
        </Field>

        <div className="grid gap-3 md:grid-cols-2">
          <Field label={`温度 ${form.temperature.toFixed(1)}`}>
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={form.temperature}
              onChange={(event) => update('temperature', Number(event.target.value))}
              className="h-9 w-full accent-signal"
            />
          </Field>
          <Field label="最大输出 tokens">
            <Input
              type="number"
              min={0}
              max={32000}
              value={form.maxTokens}
              onChange={(event) => update('maxTokens', Number(event.target.value) || 0)}
            />
          </Field>
          <Field label="单轮最多工具调用次数">
            <Input
              type="number"
              min={1}
              max={12}
              value={form.maxToolRounds}
              onChange={(event) => update('maxToolRounds', Number(event.target.value) || 1)}
            />
          </Field>
          <Field label="携带最近消息数">
            <Input
              type="number"
              min={4}
              max={100}
              value={form.historyLimit}
              onChange={(event) => update('historyLimit', Number(event.target.value) || 30)}
            />
          </Field>
        </div>

        <div className="text-[11px] leading-5 text-mist-soft">
          模型与密钥来自现有上游账号，密钥仍由服务端加密保存。助手只按当前管理员的权限调用接口；POST、PUT、PATCH、DELETE 会先暂停并等待你审批。
        </div>
      </div>
    </Dialog>
  )
}
