import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, FileText, Pencil, Sparkles, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type SkillAnalysis } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatBytes, formatTime } from '../lib/utils'

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

export function SkillDetailPage() {
  const { skillId = '' } = useParams()
  const id = Number(skillId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [analysis, setAnalysis] = useState<SkillAnalysis | null>(null)
  const [analysisPending, setAnalysisPending] = useState(false)
  const [analysisError, setAnalysisError] = useState('')
  const { data, isLoading } = useQuery({
    queryKey: ['skill', id],
    queryFn: () => api.skill(id),
    enabled: Number.isFinite(id),
  })
  const { data: listData } = useQuery({ queryKey: ['skills', '', '全部'], queryFn: () => api.skills() })
  const categories = Array.from(
    new Set(
      (listData?.categories ?? [])
        .map((item) => item.name)
        .filter((name) => name !== '全部')
        .concat(data?.category ? [data.category] : []),
    ),
  )

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteSkill(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['skills'] })
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      notifyOk('已删除')
      navigate('/skills')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  async function downloadAll() {
    if (!data) return
    try {
      const blob = await api.downloadSkill(data.id)
      triggerDownload(blob, `${data.slug}.zip`)
    } catch (caught) {
      notifyBad(errorMessage(caught, '下载失败'))
    }
  }

  async function downloadFile(path: string) {
    try {
      const blob = await api.downloadSkillFile(id, path)
      triggerDownload(blob, path.split('/').pop() || path)
    } catch (caught) {
      notifyBad(errorMessage(caught, '下载文件失败'))
    }
  }

  async function analyze() {
    setAnalysisPending(true)
    setAnalysisError('')
    try {
      setAnalysis(await api.analyzeSkill(id))
      await queryClient.invalidateQueries({ queryKey: ['skill', id] })
    } catch (caught) {
      setAnalysisError(errorMessage(caught, 'AI 分析失败'))
    } finally {
      setAnalysisPending(false)
    }
  }

  const savedAnalysis = analysis ?? data?.analysis ?? null

  if (isLoading) return <div className="py-12 text-sm text-mist">正在加载 Skill…</div>
  if (!data) return <div className="py-12 text-sm text-mist">未找到该 Skill。</div>

  return (
    <div className="space-y-5">
      <Link to="/skills" className="inline-flex items-center gap-1.5 text-sm text-mist hover:text-paper">
        <ArrowLeft size={16} /> 返回 Skills
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-2xl font-semibold">{data.name}</h1>
            <Badge tone="info">{data.category}</Badge>
          </div>
          <code className="mt-1 block text-xs text-mist">{data.slug}</code>
          <p className="mt-3 max-w-3xl text-sm text-mist">{data.description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="line" onClick={() => setEditing(true)}>
            <Pencil size={16} />
            编辑
          </Button>
          <Button onClick={() => void downloadAll()}>
            <Download size={16} />
            下载 zip
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (window.confirm(`删除 ${data.name}？`)) deleteMutation.mutate()
            }}
          >
            <Trash2 size={16} />
            删除
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-4">
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">文件</div>
          <div className="mt-3 font-mono text-3xl text-signal">{data.file_count}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">体积</div>
          <div className="mt-3 font-mono text-3xl text-info">{formatBytes(data.size_bytes)}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">更新</div>
          <div className="mt-3 text-sm text-paper">{formatTime(data.updated_at)}</div>
        </Card>
        <Card>
          <div className="text-xs uppercase tracking-[0.16em] text-mist">来源</div>
          <div className="mt-3 truncate text-sm text-paper">{data.source_name || '本地上传'}</div>
        </Card>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {data.platforms.map((platform) => (
          <Badge key={platform} tone="mist">
            {platform}
          </Badge>
        ))}
        {data.license ? <Badge tone="ok">{data.license}</Badge> : null}
        {data.version ? <Badge tone="info">v{data.version}</Badge> : null}
        {data.author ? <Badge tone="mist">{data.author}</Badge> : null}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
        <div className="grid min-w-0 gap-4">
          <section className="overflow-hidden rounded-xl border border-line">
            <div className="border-b border-line bg-panel-2 px-4 py-3 text-xs uppercase tracking-[0.16em] text-mist">SKILL.md</div>
            <pre className="max-h-[28rem] overflow-auto px-4 py-4 text-sm leading-6 text-paper whitespace-pre-wrap">{data.skill_md || '（空）'}</pre>
          </section>

          <section className="overflow-hidden rounded-xl border border-line">
            <div className="border-b border-line bg-panel-2 px-4 py-3 text-xs uppercase tracking-[0.16em] text-mist">文件列表</div>
            <div className="divide-y divide-line">
              {data.files.map((file) => (
                <div key={file.path} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText size={16} className="shrink-0 text-info" />
                    <code className="truncate text-sm">{file.path}</code>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-mist">{formatBytes(file.size)}</span>
                    <Button type="button" variant="line" onClick={() => void downloadFile(file.path)}>
                      <Download size={14} />
                      下载
                    </Button>
                  </div>
                </div>
              ))}
              {!data.files.length ? <div className="px-4 py-10 text-center text-sm text-mist">没有可下载的文件。</div> : null}
            </div>
          </section>
        </div>
        <AnalysisPanel
          analysis={savedAnalysis}
          generatedAt={data?.analysis_generated_at ?? null}
          pending={analysisPending}
          error={analysisError}
          onAnalyze={() => void analyze()}
        />
      </div>

      {editing ? (
        <EditSkillDialog
          skill={data}
          categories={categories}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false)
            await queryClient.invalidateQueries({ queryKey: ['skill', id] })
            await queryClient.invalidateQueries({ queryKey: ['skills'] })
          }}
        />
      ) : null}
    </div>
  )
}

function AnalysisPanel({
  analysis,
  generatedAt,
  pending,
  error,
  onAnalyze,
}: {
  analysis: SkillAnalysis | null
  generatedAt: string | null
  pending: boolean
  error: string
  onAnalyze: () => void
}) {
  const sections: Array<[string, string[]]> = analysis
    ? [
        ['适用场景', analysis.use_cases],
        ['核心能力', analysis.capabilities],
        ['输入与输出', analysis.inputs_outputs],
        ['触发与工作流', analysis.trigger_and_workflow],
        ['依赖与环境', analysis.dependencies],
        ['权限与风险', analysis.permissions_and_risks],
        ['限制与失败模式', analysis.limitations],
        ['配置建议', analysis.setup_suggestions],
        ['示例任务', analysis.example_tasks],
      ]
    : []
  return (
    <aside className="xl:sticky xl:top-5 overflow-hidden rounded-xl border border-line bg-panel">
      <div className="flex items-center justify-between gap-3 border-b border-line bg-panel-2 px-4 py-3">
        <div className="flex items-center gap-2 font-medium"><Sparkles size={16} className="text-signal" /> AI 分析报告</div>
        {analysis?.fit_score != null ? <Badge tone={analysis.fit_score >= 70 ? 'ok' : 'info'}>{analysis.fit_score} 分</Badge> : null}
      </div>
      <div className="grid gap-4 p-4">
        {!analysis ? <p className="text-sm leading-6 text-mist">从适用场景、能力边界、依赖、权限风险和配置建议等维度分析这个 Skill。</p> : null}
        {analysis && generatedAt ? <div className="text-xs text-mist">上次分析：{formatTime(generatedAt)}</div> : null}
        {error ? <div className="text-sm leading-6 text-danger">{error}</div> : null}
        <Button type="button" disabled={pending} onClick={onAnalyze}><Sparkles size={16} />{pending ? '分析中…' : analysis ? '重新分析' : '开始 AI 分析'}</Button>
        {analysis ? <>
          <div className="rounded-lg border border-line bg-ink p-3 text-sm leading-6 text-paper">{analysis.summary}</div>
          {sections.map(([title, values]) => <ReportSection key={title} title={title} values={values} />)}
          <div><div className="mb-1 text-xs uppercase tracking-[0.12em] text-mist">综合建议</div><div className="text-sm leading-6 text-paper">{analysis.recommendation}</div></div>
        </> : null}
      </div>
    </aside>
  )
}

function ReportSection({ title, values }: { title: string; values: string[] }) {
  return <div><div className="mb-1 text-xs uppercase tracking-[0.12em] text-mist">{title}</div><ul className="grid gap-1 text-sm leading-6 text-paper">{(values.length ? values : ['未提供']).map((value, index) => <li key={`${title}-${index}`} className="flex gap-2"><span className="text-signal">•</span><span>{value}</span></li>)}</ul></div>
}

function EditSkillDialog({
  skill,
  categories,
  onClose,
  onSaved,
}: {
  skill: { id: number; name: string; description: string; category: string }
  categories: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description)
  const [category, setCategory] = useState(skill.category)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    if (!name.trim()) {
      setError('请填写名称')
      return
    }
    setPending(true)
    setError('')
    try {
      await api.updateSkill(skill.id, {
        name: name.trim(),
        description: description.trim(),
        category,
      })
      notifyOk('已保存')
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="编辑 Skill" onClose={onClose}>
      <div className="grid gap-3">
        <Field label="名称">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="分类">
          <Select value={category} onChange={(event) => setCategory(event.target.value)}>
            {categories.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="描述">
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={5}
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-base text-paper outline-none placeholder:text-mist/70 focus:border-signal/70 md:text-sm"
          />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="button" disabled={pending} onClick={() => void save()}>
            保存
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
