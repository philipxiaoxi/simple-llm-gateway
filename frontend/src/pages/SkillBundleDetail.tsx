import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Copy, Download, Pencil, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type SkillBundleDetail, type SkillItem } from '../lib/api'
import { buildBundleInstallText } from '../lib/skillInstall'
import { notifyBad, notifyOk } from '../lib/toast'
import { copyText, errorMessage, formatBytes, formatTime } from '../lib/utils'

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

export function SkillBundleDetailPage() {
  const { bundleId } = useParams()
  const id = Number(bundleId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [adding, setAdding] = useState(false)

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['skill-bundle', id],
    queryFn: () => api.getSkillBundle(id),
    enabled: Number.isFinite(id) && id > 0,
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteSkillBundle(id),
    onSuccess: async () => {
      notifyOk('组合包已删除')
      await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
      navigate('/skills/bundles')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const removeMutation = useMutation({
    mutationFn: (skillId: number) => api.removeSkillBundleMember(id, skillId),
    onSuccess: async () => {
      notifyOk('已移除成员')
      await queryClient.invalidateQueries({ queryKey: ['skill-bundle', id] })
      await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '移除失败')),
  })

  async function downloadZip() {
    if (!data) return
    try {
      const blob = await api.downloadSkillBundle(data.id)
      triggerDownload(blob, `${data.name || 'bundle'}.zip`)
      notifyOk('组合包已开始下载')
    } catch (caught) {
      notifyBad(errorMessage(caught, '下载失败'))
    }
  }

  async function copyInstall() {
    if (!data) return
    try {
      const { url } = await api.skillBundleDownloadUrl(data.id)
      await copyText(buildBundleInstallText(data, `${window.location.origin}${url}`))
      notifyOk('安装指令已复制，链接 5 分钟内有效')
    } catch (caught) {
      notifyBad(errorMessage(caught, '复制安装指令失败'))
    }
  }

  if (isLoading) return <div className="py-12 text-sm text-mist">正在加载组合包…</div>
  if (isError) {
    return (
      <div className="space-y-4 py-12">
        <div className="text-sm text-danger">{errorMessage(error, '加载失败')}</div>
        <Button variant="line" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    )
  }
  if (!data) return <div className="py-12 text-sm text-mist">未找到该组合包。</div>

  return (
    <div className="space-y-5">
      <Link to="/skills/bundles" className="inline-flex items-center gap-1.5 text-sm text-mist hover:text-paper">
        <ArrowLeft size={16} /> 返回组合列表
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 overflow-hidden">
          <h1
            title={data.name}
            className="min-w-0 break-all text-2xl font-semibold leading-snug [overflow-wrap:anywhere]"
          >
            {data.name}
          </h1>
          <p className="mt-2 max-w-3xl break-words text-sm text-mist [overflow-wrap:anywhere]">
            {data.description || '暂无描述'}
          </p>
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-mist">
            <span>{data.member_count} 个成员</span>
            <span>更新于 {formatTime(data.updated_at)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="line" onClick={() => setEditing(true)}>
            <Pencil size={16} />
            编辑
          </Button>
          <Button variant="line" onClick={() => void copyInstall()}>
            <Copy size={16} />
            复制安装指令
          </Button>
          <Button onClick={() => void downloadZip()}>
            <Download size={16} />
            打包下载
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (window.confirm(`删除组合包「${data.name}」？不会删除其中的 Skill。`)) {
                deleteMutation.mutate()
              }
            }}
          >
            <Trash2 size={16} />
            删除
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-medium">成员</h2>
        <Button variant="line" onClick={() => setAdding(true)}>
          <Plus size={16} />
          添加 Skill
        </Button>
      </div>

      <div className="grid gap-3">
        {data.members.map((member) => (
          <Card key={`${member.skill_id}-${member.added_at}`} className="flex min-w-0 flex-wrap items-center gap-3 overflow-hidden">
            <div className="min-w-0 flex-1 overflow-hidden">
              {member.missing || !member.skill ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-warn">Skill 已失效</span>
                    <Badge tone="warn">missing</Badge>
                  </div>
                  <code className="mt-1 block text-xs text-mist">skill_id={member.skill_id}</code>
                </>
              ) : (
                <>
                  <Link
                    to={`/skills/${member.skill.id}`}
                    title={member.skill.name}
                    className="block break-all font-medium hover:text-signal [overflow-wrap:anywhere] line-clamp-2"
                  >
                    {member.skill.name}
                  </Link>
                  <code title={member.skill.slug} className="mt-1 block truncate text-xs text-mist">
                    {member.skill.slug}
                  </code>
                  <div className="mt-1 text-xs text-mist">
                    {member.skill.file_count} 个文件 · {formatBytes(member.skill.size_bytes)} · {member.skill.category}
                  </div>
                </>
              )}
            </div>
            <Button
              type="button"
              variant="line"
              onClick={() => {
                if (window.confirm('从组合包中移除该成员？')) removeMutation.mutate(member.skill_id)
              }}
            >
              移除
            </Button>
          </Card>
        ))}
      </div>

      {!data.members.length ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-12 text-center text-sm text-mist">
          还没有成员。点击「添加 Skill」从已入库列表中选择。
        </div>
      ) : null}

      {editing ? (
        <EditBundleDialog
          bundle={data}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false)
            await queryClient.invalidateQueries({ queryKey: ['skill-bundle', id] })
            await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
          }}
        />
      ) : null}

      {adding ? (
        <AddMembersDialog
          bundle={data}
          onClose={() => setAdding(false)}
          onAdded={async () => {
            setAdding(false)
            await queryClient.invalidateQueries({ queryKey: ['skill-bundle', id] })
            await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
          }}
        />
      ) : null}
    </div>
  )
}

function EditBundleDialog({
  bundle,
  onClose,
  onSaved,
}: {
  bundle: SkillBundleDetail
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(bundle.name)
  const [description, setDescription] = useState(bundle.description)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    setPending(true)
    setError('')
    try {
      await api.updateSkillBundle(bundle.id, { name, description })
      notifyOk('已保存')
      onSaved()
    } catch (caught) {
      setError(errorMessage(caught, '保存失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="编辑组合包" onClose={onClose} className="max-w-lg">
      <div className="grid gap-4">
        <Field label="名称">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="描述">
          <Input value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="line" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={pending || !name.trim()}>
            保存
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

function AddMembersDialog({
  bundle,
  onClose,
  onAdded,
}: {
  bundle: SkillBundleDetail
  onClose: () => void
  onAdded: () => void
}) {
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selected, setSelected] = useState<number[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  const existingIds = useMemo(() => new Set(bundle.members.map((item) => item.skill_id)), [bundle.members])

  const { data, isFetching } = useQuery({
    queryKey: ['skills-for-bundle', keyword],
    queryFn: () => api.skills({ q: keyword }),
  })

  const candidates = (data?.items ?? []).filter((item) => !existingIds.has(item.id))

  function toggle(id: number) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]))
  }

  async function submit() {
    if (!selected.length) {
      setError('请至少选择一个 Skill')
      return
    }
    setPending(true)
    setError('')
    try {
      const result = await api.addSkillBundleMembers(bundle.id, selected)
      const skipped = result.skipped.length
      notifyOk(skipped ? `已添加 ${result.added} 个，跳过 ${skipped} 个` : `已添加 ${result.added} 个 Skill`)
      onAdded()
    } catch (caught) {
      setError(errorMessage(caught, '添加失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="添加 Skill" onClose={onClose} className="max-w-xl">
      <div className="grid gap-4">
        <Field label="搜索已入库 Skill">
          <div className="flex gap-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setKeyword(search.trim())
              }}
              placeholder="名称、描述、slug"
            />
            <Button type="button" variant="line" onClick={() => setKeyword(search.trim())}>
              搜索
            </Button>
          </div>
        </Field>
        <div className="max-h-[50vh] space-y-2 overflow-auto rounded-xl border border-line p-2">
          {candidates.map((item: SkillItem) => {
            const checked = selected.includes(item.id)
            return (
              <label
                key={item.id}
                className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-white/5"
              >
                <input type="checkbox" checked={checked} onChange={() => toggle(item.id)} className="mt-1" />
                <span className="min-w-0 flex-1">
                  <span className="block break-all font-medium [overflow-wrap:anywhere]">{item.name}</span>
                  <code className="mt-0.5 block truncate text-xs text-mist">{item.slug}</code>
                </span>
              </label>
            )
          })}
          {!candidates.length && !isFetching ? (
            <div className="px-2 py-8 text-center text-sm text-mist">没有可添加的 Skill（可能已全部在组合中）。</div>
          ) : null}
        </div>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="line" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={pending || !selected.length}>
            添加 {selected.length ? `(${selected.length})` : ''}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
