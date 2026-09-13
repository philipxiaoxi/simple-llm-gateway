import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, ImagePlus, LoaderCircle, ScanText, Settings2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Card, Field, Input, Select } from '../components/ui'
import { api } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, errorMessage } from '../lib/utils'

export function AppOcrPage() {
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const { data: app, isLoading } = useQuery({ queryKey: ['app', 'ocr'], queryFn: () => api.app('ocr') })
  const { data: accounts = [] } = useQuery({ queryKey: ['app-binding-accounts'], queryFn: api.appBindingAccounts })
  const [preview, setPreview] = useState<string | null>(null)
  const [fileName, setFileName] = useState('')
  const [result, setResult] = useState('')
  const [meta, setMeta] = useState('')
  const [accountId, setAccountId] = useState('')
  const [model, setModel] = useState('')
  const [saving, setSaving] = useState(false)

  const selected = accounts.find((item) => String(item.id) === (accountId || String(app?.bound_account_id || '')))
  const models = selected?.models?.length ? selected.models : selected?.default_model ? [selected.default_model] : []

  const recognize = useMutation({
    mutationFn: (file: File) => api.ocrRecognize(file),
    onSuccess: (data) => {
      setResult(data.text || '')
      setMeta(`${data.model || 'model'} · ${data.ms} ms`)
      notifyOk(data.text ? '识别完成' : '未识别到文字')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '识别失败')),
  })

  async function onPick(files: FileList | null) {
    const file = files?.[0]
    if (!file) return
    if (preview) URL.revokeObjectURL(preview)
    setPreview(URL.createObjectURL(file))
    setFileName(file.name)
    setResult('')
    setMeta('')
    recognize.mutate(file)
  }

  async function saveBinding() {
    setSaving(true)
    try {
      await api.updateApp('ocr', {
        bound_account_id: accountId ? Number(accountId) : app?.bound_account_id,
        bound_model: model || null,
      })
      await queryClient.invalidateQueries({ queryKey: ['app', 'ocr'] })
      await queryClient.invalidateQueries({ queryKey: ['apps'] })
      notifyOk('绑定已保存')
    } catch (caught) {
      notifyBad(errorMessage(caught, '保存失败'))
    } finally {
      setSaving(false)
    }
  }

  if (isLoading) {
    return <Card className="text-sm text-mist">加载中…</Card>
  }

  if (!app?.enabled) {
    return (
      <Card className="grid gap-3">
        <div className="text-lg font-medium text-paper">图片 OCR 未启用</div>
        <p className="text-sm text-mist">请先到应用中心启用并配置上游账号。</p>
        <Link to="/apps">
          <Button type="button">返回应用中心</Button>
        </Link>
      </Card>
    )
  }

  return (
    <div className="grid gap-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm text-mist">
            <Link to="/apps" className="hover:text-paper">
              应用中心
            </Link>
            <span>/</span>
            <span>图片 OCR</span>
          </div>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-paper">
            <ScanText size={22} className="text-signal" /> 图片 OCR
          </h1>
          <p className="mt-1 text-sm text-mist">上传图片，调用已绑定的视觉模型提取文字。</p>
        </div>
        <Link to="/apps">
          <Button type="button" variant="line">
            <Settings2 size={16} /> 应用配置
          </Button>
        </Link>
      </div>

      <Card className="grid gap-3">
        <div className="text-sm font-medium text-paper">模型绑定</div>
        <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
          <Field label="上游账号">
            <Select
              value={accountId || (app.bound_account_id ? String(app.bound_account_id) : '')}
              onChange={(event) => {
                setAccountId(event.target.value)
                const next = accounts.find((item) => String(item.id) === event.target.value)
                setModel(next?.default_model || '')
              }}
            >
              <option value="">请选择账号</option>
              {accounts.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.available}>
                  {item.name} ({item.provider})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="模型">
            <Select
              value={model || app.bound_model || ''}
              onChange={(event) => setModel(event.target.value)}
            >
              <option value="">使用账号默认模型</option>
              {models.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex items-end">
            <Button type="button" variant="line" disabled={saving} onClick={() => void saveBinding()}>
              保存绑定
            </Button>
          </div>
        </div>
        {!app.bound_account_id ? (
          <div className="text-sm text-warn">尚未绑定上游账号，识别前请先选择并保存。</div>
        ) : (
          <div className="text-xs text-mist">
            当前绑定：账号 #{app.bound_account_id}
            {app.bound_model ? ` · ${app.bound_model}` : ' · 默认模型'}
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="grid gap-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-paper">上传图片</div>
            {recognize.isPending ? (
              <Badge tone="warn">
                <LoaderCircle size={12} className="animate-spin" /> 识别中
              </Badge>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line bg-white/[0.02] px-4 py-8 text-sm text-mist transition hover:border-signal/40 hover:text-paper"
          >
            <ImagePlus size={28} className="text-signal" />
            <span>点击选择或拖入图片</span>
            <span className="text-xs">支持 jpg / png / webp / gif，默认上限 8MB</span>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              void onPick(event.target.files)
              event.target.value = ''
            }}
          />
          {preview ? (
            <div className="overflow-hidden rounded-lg border border-line">
              <img src={preview} alt={fileName || 'preview'} className="max-h-80 w-full object-contain bg-ink" />
            </div>
          ) : null}
          {fileName ? <div className="text-xs text-mist">{fileName}</div> : null}
        </Card>

        <Card className="grid gap-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-medium text-paper">识别结果</div>
            <div className="flex items-center gap-2">
              {meta ? <span className="text-xs text-mist">{meta}</span> : null}
              <Button
                type="button"
                variant="line"
                disabled={!result}
                onClick={() => {
                  void copyText(result).then((ok) => (ok ? notifyOk('已复制') : notifyBad('复制失败')))
                }}
              >
                <Copy size={16} /> 复制
              </Button>
            </div>
          </div>
          <Field label="文本">
            <textarea
              className="min-h-64 w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper outline-none focus:border-signal/70"
              value={result}
              onChange={(event) => setResult(event.target.value)}
              placeholder="识别结果会显示在这里"
            />
          </Field>
          <Input value={result ? `${result.length} 字` : ''} readOnly className="text-mist" />
        </Card>
      </div>
    </div>
  )
}
