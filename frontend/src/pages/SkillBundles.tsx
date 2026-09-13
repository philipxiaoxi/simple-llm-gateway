import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Layers, Plus, Search, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type SkillBundleItem } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

export function SkillBundlesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [keyword, setKeyword] = useState('')
  const [createOpen, setCreateOpen] = useState(false)

  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['skill-bundles', keyword],
    queryFn: () => api.listSkillBundles(keyword),
  })

  const items = data?.items ?? []

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSkillBundle(id),
    onSuccess: async () => {
      notifyOk('组合包已删除')
      await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  return (
    <div className="space-y-5">
      <Link to="/skills" className="inline-flex items-center gap-1.5 text-sm text-mist hover:text-paper">
        <ArrowLeft size={16} /> 返回 Skills
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">Skills 组合</h1>
          <p className="mt-1 text-sm text-mist">把已入库的多个 Skill 编成组合包，支持整包下载与复制安装指令。</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus size={16} />
          新建组合包
        </Button>
      </div>

      <Card className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
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
              placeholder="搜索名称、描述"
            />
          </div>
        </Field>
        <Button type="button" variant="line" onClick={() => setKeyword(search.trim())}>
          搜索
        </Button>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <BundleCard
            key={item.id}
            item={item}
            onOpen={() => navigate(`/skills/bundles/${item.id}`)}
            onDelete={() => {
              if (window.confirm(`删除组合包「${item.name}」？不会删除其中的 Skill。`)) {
                deleteMutation.mutate(item.id)
              }
            }}
          />
        ))}
      </div>

      {isError ? (
        <div className="rounded-xl border border-dashed border-danger/40 px-6 py-16 text-center">
          <div className="text-sm text-danger">{errorMessage(error, '加载组合包失败')}</div>
          <Button type="button" variant="line" className="mt-4" onClick={() => void refetch()}>
            重试
          </Button>
        </div>
      ) : null}

      {!items.length && !isFetching && !isError ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-16 text-center">
          <Layers className="mx-auto text-mist" />
          <div className="mt-3 text-sm text-mist">还没有组合包。先创建一个，再添加已入库的 Skill。</div>
          <Button type="button" className="mt-4" onClick={() => setCreateOpen(true)}>
            <Plus size={16} /> 新建组合包
          </Button>
        </div>
      ) : null}

      {createOpen ? (
        <CreateBundleDialog
          onClose={() => setCreateOpen(false)}
          onCreated={async (id) => {
            setCreateOpen(false)
            await queryClient.invalidateQueries({ queryKey: ['skill-bundles'] })
            navigate(`/skills/bundles/${id}`)
          }}
        />
      ) : null}
    </div>
  )
}

function BundleCard({
  item,
  onOpen,
  onDelete,
}: {
  item: SkillBundleItem
  onOpen: () => void
  onDelete: () => void
}) {
  return (
    <Card className="flex min-w-0 flex-col gap-3 overflow-hidden">
      <div className="min-w-0">
        <button
          type="button"
          onClick={onOpen}
          title={item.name}
          className="block w-full break-all text-left text-lg font-semibold leading-snug hover:text-signal [overflow-wrap:anywhere] line-clamp-2"
        >
          {item.name}
        </button>
        <p className="mt-2 line-clamp-3 break-words text-sm text-mist [overflow-wrap:anywhere]">
          {item.description || '暂无描述'}
        </p>
      </div>
      <div className="mt-auto flex min-w-0 items-center justify-between gap-2 text-xs text-mist">
        <span>{item.member_count} 个成员</span>
        <span className="shrink-0">{formatTime(item.updated_at)}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="line" className="min-w-0 flex-1" onClick={onOpen}>
          详情
        </Button>
        <Button type="button" variant="danger" onClick={onDelete}>
          <Trash2 size={16} />
        </Button>
      </div>
    </Card>
  )
}

function CreateBundleDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    setPending(true)
    setError('')
    try {
      const item = await api.createSkillBundle({ name, description })
      notifyOk('组合包已创建')
      onCreated(item.id)
    } catch (caught) {
      setError(errorMessage(caught, '创建失败'))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog title="新建组合包" onClose={onClose} className="max-w-lg">
      <div className="grid gap-4">
        <Field label="名称">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：办公效率套件" />
        </Field>
        <Field label="描述">
          <Input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="可选" />
        </Field>
        {error ? <div className="text-sm text-danger">{error}</div> : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="line" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button type="button" onClick={() => void submit()} disabled={pending || !name.trim()}>
            {pending ? '创建中…' : '创建'}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
