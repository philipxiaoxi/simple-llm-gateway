import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FolderUp, RefreshCw, Search, Sparkles, Tags, Trash2, Upload } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type Account, type SkillCategoryItem, type SkillItem } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatBytes, formatTime } from '../lib/utils'

const AUTO_CATEGORY = '自动识别'

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function FilePicker({
  onPicked,
  disabled,
}: {
  onPicked: (files: File[]) => void
  disabled?: boolean
}) {
  const zipRef = useRef<HTMLInputElement>(null)
  const dirRef = useRef<HTMLInputElement>(null)
  return <>
    <div className="grid gap-2 sm:grid-cols-2">
      <Button type="button" variant="line" disabled={disabled} onClick={() => zipRef.current?.click()}>
        <Upload size={16} /> 上传压缩包
      </Button>
      <Button type="button" variant="line" disabled={disabled} onClick={() => dirRef.current?.click()}>
        <FolderUp size={16} /> 上传目录
      </Button>
    </div>
    <input ref={zipRef} type="file" accept=".zip,.tar,.tgz,.gz,application/zip,application/x-tar,application/gzip" className="hidden" onChange={(event) => {
      onPicked(Array.from(event.target.files ?? []))
      event.target.value = ''
    }} />
    <input ref={dirRef} type="file" className="hidden" multiple // @ts-expect-error webkitdirectory is not in the React type yet
      webkitdirectory="" directory="" onChange={(event) => {
        onPicked(Array.from(event.target.files ?? []))
        event.target.value = ''
      }} />
  </>
}

function UploadDialog({
  categories,
  onClose,
  onUploaded,
}: {
  categories: string[]
  onClose: () => void
  onUploaded: () => void
}) {
  const [category, setCategory] = useState(AUTO_CATEGORY)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<File[]>([])

  async function submit(files: File[]) {
    if (!files.length) {
      setError('请选择 zip / tar 压缩包，或一个包含 SKILL.md 的目录')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.uploadSkills(files, category)
      const skipped = result.skipped.length
      if (result.created === 0 && skipped) {
        setError(result.skipped.map((item) => `${item.name}：${item.reason}`).join('；'))
        return
      }
      notifyOk(
        skipped
          ? `已导入 ${result.created} 个 Skill，跳过 ${skipped} 个`
          : `已导入 ${result.created} 个 Skill`,
      )
      onUploaded()
    } catch (caught) {
      setError(errorMessage(caught, '上传失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="上传 Skills" onClose={onClose} className="max-w-xl">
      <div className="grid gap-4">
        <p className="text-sm text-mist">
          支持单个 Skill 目录、包含多个 Skill 的目录，以及 zip / tar / tar.gz。每个 Skill 都需要有{' '}
          <code className="text-paper">SKILL.md</code>。
        </p>
        <Field label="分类">
          <Select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value={AUTO_CATEGORY}>{AUTO_CATEGORY}</option>
            {categories.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
        </Field>
        <FilePicker onPicked={setPicked} disabled={pending} />
        {picked.length ? (
          <div className="rounded-md border border-line bg-ink px-3 py-2 text-sm text-mist">
            已选择 {picked.length} 个文件
            {picked[0] ? `，例如 ${picked[0].name}` : ''}
          </div>
        ) : null}
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending || !picked.length} onClick={() => void submit(picked)}>
            {pending ? '导入中…' : '开始导入'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

export function SkillsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState('全部')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [bulkUpdateOpen, setBulkUpdateOpen] = useState(false)
  const [replaceSkill, setReplaceSkill] = useState<SkillItem | null>(null)
  const [manageOpen, setManageOpen] = useState(false)
  const [aiConfigOpen, setAiConfigOpen] = useState(false)
  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['skills', keyword, category],
    queryFn: () => api.skills({ q: keyword, category: category === '全部' ? '' : category }),
  })
  const items = data?.items ?? []
  const categories = data?.categories ?? []
  const categoryNames = useMemo(
    () => categories.filter((item) => item.name !== '全部').map((item) => item.name),
    [categories],
  )
  const usedCategoryCount = categoryNames.filter(
    (name) => (categories.find((item) => item.name === name)?.count ?? 0) > 0,
  ).length
  const totalCount = data?.categories.find((item) => item.name === '全部')?.count ?? 0

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSkill(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['skills'] })
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      notifyOk('已删除')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  async function download(item: SkillItem) {
    try {
      const blob = await api.downloadSkill(item.id)
      triggerDownload(blob, `${item.slug}.zip`)
      notifyOk(`已开始下载 ${item.slug}.zip`)
    } catch (caught) {
      notifyBad(errorMessage(caught, '下载失败'))
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Skills</h1>
          <p className="mt-1 text-sm text-mist">
            本地 Agent Skills 仓库。上传符合 SKILL.md 规范的目录或压缩包，再按分类浏览和下载。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="line" onClick={() => setAiConfigOpen(true)}>
            <Sparkles size={16} />
            AI 配置
          </Button>
          <Button variant="line" onClick={() => setManageOpen(true)}>
            <Tags size={16} />
            分类管理
          </Button>
          <Button variant="line" onClick={() => setBulkUpdateOpen(true)}>
            <RefreshCw size={16} />
            批量更新
          </Button>
          <Button onClick={() => setUploadOpen(true)}>
            <Upload size={16} />
            上传
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">已收录</div>
          <div className="mt-3 font-mono text-3xl text-signal">{data ? totalCount : '—'}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">当前筛选</div>
          <div className="mt-3 font-mono text-3xl text-info">{data ? data.total : '—'}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">已用分类</div>
          <div className="mt-3 font-mono text-3xl text-paper">
            {data ? usedCategoryCount : '—'}
            {data ? <span className="ml-1 text-lg text-mist">/ {categoryNames.length}</span> : null}
          </div>
        </Card>
      </div>

      <Card className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-end">
        <Field label="搜索">
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
            <Input
              className="pl-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setKeyword(search.trim())
              }}
              placeholder="搜索名称、描述、作者"
            />
          </div>
        </Field>
        <Field label="分类">
          <Select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            {(categories.length ? categories : [{ name: '全部', count: 0 }]).map((item) => (
              <option key={item.name} value={item.name}>
                {item.name} ({item.count})
              </option>
            ))}
          </Select>
        </Field>
        <Button type="button" variant="line" onClick={() => setKeyword(search.trim())}>
          搜索
        </Button>
      </Card>

      <div className="flex flex-wrap gap-2">
        {categories.map((item) => (
          <button
            key={item.name}
            type="button"
            onClick={() => setCategory(item.name)}
            className={`rounded-full px-3 py-1 text-xs transition ${
              category === item.name ? 'bg-signal/15 text-signal' : 'bg-white/5 text-mist hover:text-paper'
            }`}
          >
            {item.name} {item.count}
          </button>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <Card key={item.id} className="flex flex-col gap-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Link to={`/skills/${item.id}`} className="block truncate text-lg font-semibold hover:text-signal">
                  {item.name}
                </Link>
                <code className="mt-1 block truncate text-xs text-mist">{item.slug}</code>
              </div>
              <Badge tone="info">{item.category}</Badge>
            </div>
            <p className="line-clamp-3 text-sm text-mist">{item.description || '暂无描述'}</p>
            <div className="flex flex-wrap gap-1.5">
              {item.platforms.slice(0, 4).map((platform) => (
                <Badge key={platform} tone="mist">
                  {platform}
                </Badge>
              ))}
              {item.license ? <Badge tone="ok">{item.license}</Badge> : null}
            </div>
            <div className="mt-auto flex items-center justify-between gap-2 text-xs text-mist">
              <span>
                {item.file_count} 个文件 · {formatBytes(item.size_bytes)}
              </span>
              <span>{formatTime(item.updated_at)}</span>
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="line" className="flex-1" onClick={() => navigate(`/skills/${item.id}`)}>
                详情
              </Button>
              <Button type="button" variant="line" title="重新上传并覆盖此 Skill" onClick={() => setReplaceSkill(item)}>
                <RefreshCw size={16} />
              </Button>
              <Button type="button" variant="line" onClick={() => void download(item)}>
                <Download size={16} />
                下载
              </Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  if (window.confirm(`删除 ${item.name}？`)) deleteMutation.mutate(item.id)
                }}
              >
                <Trash2 size={16} />
              </Button>
            </div>
          </Card>
        ))}
      </div>
      {isError ? (
        <div className="rounded-xl border border-dashed border-danger/40 px-6 py-16 text-center">
          <Sparkles className="mx-auto text-mist" />
          <div className="mt-3 text-sm text-danger">{errorMessage(error, '加载 Skills 失败')}</div>
          <Button type="button" variant="line" className="mt-4" onClick={() => void refetch()}>
            重试
          </Button>
        </div>
      ) : null}
      {!items.length && !isFetching && !isError ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-16 text-center">
          <Sparkles className="mx-auto text-mist" />
          <div className="mt-3 text-sm text-mist">还没有 Skill。先上传一个包含 SKILL.md 的目录或压缩包。</div>
        </div>
      ) : null}

      {uploadOpen ? (
        <UploadDialog
          categories={categoryNames}
          onClose={() => setUploadOpen(false)}
          onUploaded={async () => {
            setUploadOpen(false)
            await queryClient.invalidateQueries({ queryKey: ['skills'] })
            await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
          }}
        />
      ) : null}
      {bulkUpdateOpen ? (
        <BulkUpdateDialog
          categories={categoryNames}
          onClose={() => setBulkUpdateOpen(false)}
          onUpdated={async () => {
            setBulkUpdateOpen(false)
            await queryClient.invalidateQueries({ queryKey: ['skills'] })
            await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
          }}
        />
      ) : null}
      {replaceSkill ? (
        <ReplaceSkillDialog
          skill={replaceSkill}
          categories={categoryNames}
          onClose={() => setReplaceSkill(null)}
          onUpdated={async () => {
            setReplaceSkill(null)
            await queryClient.invalidateQueries({ queryKey: ['skills'] })
            await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
          }}
        />
      ) : null}
      {manageOpen ? (
        <CategoryManageDialog
          onClose={() => setManageOpen(false)}
          onChanged={async () => {
            await queryClient.invalidateQueries({ queryKey: ['skills'] })
            await queryClient.invalidateQueries({ queryKey: ['skill-categories'] })
          }}
        />
      ) : null}
      {aiConfigOpen ? <AiClassificationDialog onClose={() => setAiConfigOpen(false)} /> : null}
    </div>
  )
}

function BulkUpdateDialog({
  categories,
  onClose,
  onUpdated,
}: {
  categories: string[]
  onClose: () => void
  onUpdated: () => Promise<void>
}) {
  const [category, setCategory] = useState(AUTO_CATEGORY)
  const [picked, setPicked] = useState<File[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    if (!picked.length) {
      setError('请选择包含已有 Skill 的目录或压缩包')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.bulkUpdateSkills(picked, category)
      if (result.created === 0 && result.skipped.length) {
        setError(result.skipped.map((item) => `${item.name}：${item.reason}`).join('；'))
        return
      }
      notifyOk(result.skipped.length ? `已更新 ${result.created} 个，跳过 ${result.skipped.length} 个` : `已更新 ${result.created} 个 Skill`)
      await onUpdated()
    } catch (caught) {
      setError(errorMessage(caught, '批量更新失败'))
    } finally {
      setPending(false)
    }
  }

  return <Dialog title="批量更新 Skills" onClose={onClose} className="max-w-xl">
    <div className="grid gap-4">
      <p className="text-sm leading-6 text-mist">按 SKILL.md 中的 slug 匹配已有 Skill 并原地覆盖。未匹配的 Skill 不会新增，只会列入跳过结果。</p>
      <Field label="分类">
        <Select value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value={AUTO_CATEGORY}>{AUTO_CATEGORY}</option>
          {categories.map((item) => <option key={item} value={item}>{item}</option>)}
        </Select>
      </Field>
      <FilePicker onPicked={setPicked} disabled={pending} />
      {picked.length ? <div className="rounded-md border border-line bg-ink px-3 py-2 text-sm text-mist">已选择 {picked.length} 个文件</div> : null}
      {error ? <div className="text-sm text-danger">{error}</div> : null}
      <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>取消</Button><Button type="button" disabled={pending} onClick={() => void submit()}>{pending ? '更新中…' : '开始更新'}</Button></div>
    </div>
  </Dialog>
}

function ReplaceSkillDialog({
  skill,
  categories,
  onClose,
  onUpdated,
}: {
  skill: SkillItem
  categories: string[]
  onClose: () => void
  onUpdated: () => Promise<void>
}) {
  const [category, setCategory] = useState(skill.category)
  const [picked, setPicked] = useState<File[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    if (!picked.length) {
      setError('请选择新的 Skill 目录或压缩包')
      return
    }
    setPending(true)
    setError('')
    try {
      await api.replaceSkill(skill.id, picked, category)
      notifyOk(`已覆盖 ${skill.name}`)
      await onUpdated()
    } catch (caught) {
      setError(errorMessage(caught, '重新上传失败'))
    } finally {
      setPending(false)
    }
  }

  return <Dialog title={`重新上传：${skill.name}`} onClose={onClose} className="max-w-xl">
    <div className="grid gap-4">
      <p className="text-sm leading-6 text-mist">会保留当前 Skill 的数据库 ID，并替换文件、元数据和分类。原有 AI 分析报告会清除，需要重新分析。</p>
      <Field label="分类">
        <Select value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value={AUTO_CATEGORY}>{AUTO_CATEGORY}</option>
          {categories.map((item) => <option key={item} value={item}>{item}</option>)}
        </Select>
      </Field>
      <FilePicker onPicked={setPicked} disabled={pending} />
      {picked.length ? <div className="rounded-md border border-line bg-ink px-3 py-2 text-sm text-mist">已选择 {picked.length} 个文件</div> : null}
      {error ? <div className="text-sm text-danger">{error}</div> : null}
      <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>取消</Button><Button type="button" disabled={pending} onClick={() => void submit()}>{pending ? '覆盖中…' : '确认覆盖'}</Button></div>
    </div>
  </Dialog>
}

function parseKeywordInput(value: string) {
  return Array.from(
    new Set(
      value
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  )
}

function AiClassificationDialog({ onClose }: { onClose: () => void }) {
  const { data: classification, isLoading: classificationLoading } = useQuery({
    queryKey: ['skill-classification-settings'],
    queryFn: () => api.skillClassificationSettings(),
  })
  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ['accounts'],
    queryFn: api.accounts,
  })
  const [accountId, setAccountId] = useState('')
  const [model, setModel] = useState('')
  const [customModel, setCustomModel] = useState(false)
  const [enabled, setEnabled] = useState(false)
  const [reportAccountId, setReportAccountId] = useState('')
  const [reportModel, setReportModel] = useState('')
  const [customReportModel, setCustomReportModel] = useState(false)
  const [reportEnabled, setReportEnabled] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!classification) return
    setAccountId(classification.account_id ? String(classification.account_id) : '')
    setModel(classification.model || '')
    const account = accounts.find((item) => item.id === classification.account_id)
    setCustomModel(Boolean(classification.model && account?.models.length && !account.models.includes(classification.model)))
    setEnabled(classification.enabled)
    setReportAccountId(classification.report_account_id ? String(classification.report_account_id) : '')
    setReportModel(classification.report_model || '')
    const reportAccount = accounts.find((item) => item.id === classification.report_account_id)
    setCustomReportModel(Boolean(classification.report_model && reportAccount?.models.length && !reportAccount.models.includes(classification.report_model)))
    setReportEnabled(classification.report_enabled)
  }, [classification, accounts])

  async function save() {
    setPending(true)
    setError('')
    try {
      await api.updateSkillClassificationSettings({
        account_id: accountId ? Number(accountId) : null,
        model: model.trim() || null,
        enabled,
        report_account_id: reportAccountId ? Number(reportAccountId) : null,
        report_model: reportModel.trim() || null,
        report_enabled: reportEnabled,
      })
      notifyOk(enabled ? '已启用 AI 自动识别' : '已保存 AI 配置')
      onClose()
    } catch (caught) {
      setError(errorMessage(caught, '保存 AI 配置失败'))
    } finally {
      setPending(false)
    }
  }

  const availableAccounts = accounts.filter((item) => item.status === 'active' && item.source !== 'agent')
  const selectedAccount = availableAccounts.find((item) => item.id === Number(accountId))
  const selectedReportAccount = availableAccounts.find((item) => item.id === Number(reportAccountId))

  return (
    <Dialog title="AI 配置" onClose={onClose} className="max-w-xl">
      <div className="grid gap-4">
        <p className="text-sm leading-6 text-mist">分别配置 Skill 自动分类和详情页 AI 分析报告。两者可以使用不同账号和模型。</p>
        <div className="border-b border-line pb-4">
          <div className="mb-3 flex items-center gap-2 font-medium"><Sparkles size={16} className="text-signal" />自动分类</div>
        <Field label="站点账号">
          <Select value={accountId} disabled={classificationLoading || pending} onChange={(event) => {
            const nextId = event.target.value
            const nextAccount = availableAccounts.find((item) => item.id === Number(nextId))
            setAccountId(nextId)
            setCustomModel(false)
            setModel(nextAccount?.models[0] || '')
          }}>
            <option value="">不使用 AI</option>
            {availableAccounts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
        </Field>
        <Field label="模型">
          {customModel ? (
            <Input value={model} disabled={pending} onChange={(event) => setModel(event.target.value)} placeholder="输入模型 ID" />
          ) : (
            <Select value={model} disabled={pending || !accountId} onChange={(event) => {
              if (event.target.value === '__custom__') {
                setCustomModel(true)
                setModel('')
              } else setModel(event.target.value)
            }}>
              <option value="">使用账号默认模型</option>
              {(selectedAccount?.models ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
              <option value="__custom__">自定义模型...</option>
            </Select>
          )}
        </Field>
        {!selectedAccount?.models.length && accountId ? <div className="text-xs text-mist">该账号暂无已同步模型，可选择自定义模型。</div> : null}
        <label className="flex items-center gap-2 text-sm text-paper">
          <input type="checkbox" checked={enabled && Boolean(accountId)} disabled={pending || !accountId} onChange={(event) => setEnabled(event.target.checked)} />
          启用 AI 自动识别
        </label>
        </div>
        <div className="grid gap-4">
          <div className="flex items-center gap-2 font-medium"><Sparkles size={16} className="text-info" />分析报告</div>
          <p className="text-sm leading-6 text-mist">在 Skill 详情页点击「开始 AI 分析」时，使用下面配置生成报告。</p>
          <Field label="报告账号">
            <Select value={reportAccountId} disabled={classificationLoading || pending} onChange={(event) => {
              const nextId = event.target.value
              const nextAccount = availableAccounts.find((item) => item.id === Number(nextId))
              setReportAccountId(nextId)
              setCustomReportModel(false)
              setReportModel(nextAccount?.models[0] || '')
            }}>
              <option value="">不使用 AI 报告</option>
              {availableAccounts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </Select>
          </Field>
          <Field label="报告模型">
            {customReportModel ? (
              <Input value={reportModel} disabled={pending} onChange={(event) => setReportModel(event.target.value)} placeholder="输入模型 ID" />
            ) : (
              <Select value={reportModel} disabled={pending || !reportAccountId} onChange={(event) => {
                if (event.target.value === '__custom__') {
                  setCustomReportModel(true)
                  setReportModel('')
                } else setReportModel(event.target.value)
              }}>
                <option value="">使用账号默认模型</option>
                {(selectedReportAccount?.models ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
                <option value="__custom__">自定义模型...</option>
              </Select>
            )}
          </Field>
          {!selectedReportAccount?.models.length && reportAccountId ? <div className="text-xs text-mist">该账号暂无已同步模型，可选择自定义模型。</div> : null}
          <label className="flex items-center gap-2 text-sm text-paper">
            <input type="checkbox" checked={reportEnabled && Boolean(reportAccountId)} disabled={pending || !reportAccountId} onChange={(event) => setReportEnabled(event.target.checked)} />
            启用 AI 分析报告
          </label>
        </div>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>取消</Button>
          <Button type="button" disabled={pending} onClick={() => void save()}>{pending ? '保存中…' : '保存配置'}</Button>
        </div>
      </div>
    </Dialog>
  )
}

function CategoryManageDialog({
  onClose,
  onChanged,
}: {
  onClose: () => void
  onChanged: () => Promise<void> | void
}) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['skill-categories'],
    queryFn: () => api.skillCategories(),
  })
  const [name, setName] = useState('')
  const [keywords, setKeywords] = useState('')
  const [pending, setPending] = useState(false)
  const [formError, setFormError] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editKeywords, setEditKeywords] = useState('')
  const items = data?.items ?? []

  async function createCategory() {
    if (!name.trim()) {
      setFormError('请填写分类名称')
      return
    }
    setPending(true)
    setFormError('')
    try {
      await api.createSkillCategory({ name: name.trim(), keywords: parseKeywordInput(keywords) })
      setName('')
      setKeywords('')
      notifyOk('已新增分类')
      await refetch()
      await onChanged()
    } catch (caught) {
      setFormError(errorMessage(caught, '新增分类失败'))
    } finally {
      setPending(false)
    }
  }

  function startEdit(item: SkillCategoryItem) {
    setEditingId(item.id)
    setEditName(item.name)
    setEditKeywords(item.keywords.join(', '))
    setFormError('')
  }

  async function saveEdit(item: SkillCategoryItem) {
    if (!editName.trim()) {
      setFormError('请填写分类名称')
      return
    }
    setPending(true)
    setFormError('')
    try {
      await api.updateSkillCategory(item.id, {
        name: item.is_protected ? undefined : editName.trim(),
        keywords: parseKeywordInput(editKeywords),
      })
      setEditingId(null)
      notifyOk('已保存分类')
      await refetch()
      await onChanged()
    } catch (caught) {
      setFormError(errorMessage(caught, '保存分类失败'))
    } finally {
      setPending(false)
    }
  }

  async function removeCategory(item: SkillCategoryItem) {
    if (item.is_protected) return
    if (!window.confirm(item.count ? `删除「${item.name}」？其中 ${item.count} 个 Skill 会改到「其他」。` : `删除「${item.name}」？`)) {
      return
    }
    setPending(true)
    setFormError('')
    try {
      await api.deleteSkillCategory(item.id)
      notifyOk('已删除分类')
      await refetch()
      await onChanged()
    } catch (caught) {
      setFormError(errorMessage(caught, '删除分类失败'))
    } finally {
      setPending(false)
    }
  }


  return (
    <Dialog title="分类管理" onClose={onClose} className="max-w-2xl">
      <div className="grid gap-4">
        <p className="text-sm text-mist">
          新增、改名或删除分类。删除后，该分类下的 Skill 会改到「其他」。
        </p>
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] md:items-end">
          <Field label="新分类">
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 内部工具" />
          </Field>
          <Field label="关键词">
            <Input
              value={keywords}
              onChange={(event) => setKeywords(event.target.value)}
              placeholder="逗号分隔，用于自动识别"
            />
          </Field>
          <Button type="button" disabled={pending} onClick={() => void createCategory()}>
            新增
          </Button>
        </div>
        {formError ? <div className="text-sm text-danger">{formError}</div> : null}
        {isLoading ? <div className="text-sm text-mist">正在加载分类…</div> : null}
        {isError ? <div className="text-sm text-danger">{errorMessage(error, '加载分类失败')}</div> : null}
        <div className="max-h-[28rem] divide-y divide-line overflow-auto rounded-xl border border-line">
          {items.map((item) => (
            <div key={item.id} className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
              {editingId === item.id ? (
                <div className="grid gap-2">
                  <Input value={editName} disabled={item.is_protected} onChange={(event) => setEditName(event.target.value)} />
                  <Input
                    value={editKeywords}
                    onChange={(event) => setEditKeywords(event.target.value)}
                    placeholder="关键词，逗号分隔"
                  />
                </div>
              ) : (
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{item.name}</span>
                    <Badge tone="mist">{item.count}</Badge>
                    {item.is_protected ? <Badge tone="info">系统</Badge> : null}
                  </div>
                  <div className="mt-1 truncate text-xs text-mist">
                    {item.keywords.length ? item.keywords.join('、') : '未设置自动识别关键词'}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap justify-end gap-2">
                {editingId === item.id ? (
                  <>
                    <Button type="button" variant="ghost" disabled={pending} onClick={() => setEditingId(null)}>
                      取消
                    </Button>
                    <Button type="button" disabled={pending} onClick={() => void saveEdit(item)}>
                      保存
                    </Button>
                  </>
                ) : (
                  <>
                    <Button type="button" variant="line" disabled={pending} onClick={() => startEdit(item)}>
                      编辑
                    </Button>
                    <Button type="button" variant="danger" disabled={pending || item.is_protected} onClick={() => void removeCategory(item)}>
                      <Trash2 size={14} />
                      删除
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))}
          {!items.length && !isLoading ? <div className="px-4 py-10 text-center text-sm text-mist">还没有分类。</div> : null}
        </div>
      </div>
    </Dialog>
  )
}
