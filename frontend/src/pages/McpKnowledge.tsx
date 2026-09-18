import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, ListChecks, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type McpKnowledgeEmbeddingAccount } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage, formatTime } from '../lib/utils'

const SCOPE_OPTIONS = [
  { value: 'public', label: '公开：所有 MCP Key 可读' },
  { value: 'restricted', label: '受限：仅指定 MCP Key 可读' },
  { value: 'private', label: '私有：仅管理后台可用' },
]

function ScopeFields({
  scope,
  allowedKeyIds,
  keys,
  onScopeChange,
  onAllowedChange,
}: {
  scope: string
  allowedKeyIds: number[]
  keys: { id: number; name: string }[]
  onScopeChange: (scope: string) => void
  onAllowedChange: (ids: number[]) => void
}) {
  return (
    <>
      <Field label="访问范围（MCP 下游）">
        <select
          className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
          value={scope}
          onChange={(e) => onScopeChange(e.target.value)}
        >
          {SCOPE_OPTIONS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </Field>
      {scope === 'restricted' ? (
        <Field label="允许的 MCP Key">
          <div className="max-h-40 space-y-1 overflow-auto rounded-md border border-line p-2">
            {keys.map((key) => (
              <label key={key.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={allowedKeyIds.includes(key.id)}
                  onChange={(e) =>
                    onAllowedChange(
                      e.target.checked ? [...allowedKeyIds, key.id] : allowedKeyIds.filter((id) => id !== key.id),
                    )
                  }
                />
                {key.name} (#{key.id})
              </label>
            ))}
            {!keys.length ? <div className="text-xs text-mist">还没有 MCP Key</div> : null}
          </div>
        </Field>
      ) : null}
    </>
  )
}

function EmbeddingFields({
  accounts,
  accountId,
  model,
  dimensions,
  onAccountChange,
  onModelChange,
  onDimensionsChange,
}: {
  accounts: McpKnowledgeEmbeddingAccount[]
  accountId: number | ''
  model: string
  dimensions: string
  onAccountChange: (id: number | '') => void
  onModelChange: (model: string) => void
  onDimensionsChange: (dimensions: string) => void
}) {
  const selected = accounts.find((item) => item.id === accountId)
  const models = selected?.models ?? []

  return (
    <>
      <Field label="向量上游账号">
        <select
          className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
          value={accountId === '' ? '' : String(accountId)}
          onChange={(e) => {
            const value = e.target.value
            if (!value) {
              onAccountChange('')
              onModelChange('')
              return
            }
            const id = Number(value)
            onAccountChange(id)
            const acc = accounts.find((item) => item.id === id)
            onModelChange(acc?.default_model || acc?.models[0] || '')
          }}
        >
          <option value="">不绑定（用全局 Fake / env）</option>
          {accounts.map((item) => (
            <option key={item.id} value={item.id} disabled={!item.available}>
              {item.name} ({item.provider}){item.available ? '' : ' · 不可用'}
            </option>
          ))}
        </select>
      </Field>
      <Field label="向量模型">
        {models.length ? (
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            disabled={!accountId}
          >
            <option value="">请选择模型</option>
            {models.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        ) : (
          <Input
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder={accountId ? '手动填写 embedding 模型名' : '先选账号或留空'}
            disabled={!accountId && !model}
          />
        )}
      </Field>
      <Field label="向量维度（可选）">
        <Input
          value={dimensions}
          onChange={(e) => onDimensionsChange(e.target.value.replace(/[^0-9]/g, ''))}
          placeholder="留空用模型默认，如 512 / 1024 / 2048"
          inputMode="numeric"
        />
      </Field>
      <p className="text-xs text-mist">
        绑定后，入库与向量检索会调用该账号的 OpenAI 兼容 <code>/embeddings</code>。换模型或维度会让已有向量失效，需要执行「重建向量」。
      </p>
    </>
  )
}

export function McpKnowledgePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [accountId, setAccountId] = useState<number | ''>('')
  const [model, setModel] = useState('')
  const [dimensions, setDimensions] = useState('')
  const [scope, setScope] = useState('public')
  const [allowedKeyIds, setAllowedKeyIds] = useState<number[]>([])

  const { data = [], isFetching, isError, error, refetch } = useQuery({
    queryKey: ['mcp-knowledge-bases'],
    queryFn: () => api.mcpKnowledgeBases(),
  })

  const accountsQuery = useQuery({
    queryKey: ['mcp-knowledge-embedding-accounts'],
    queryFn: () => api.mcpKnowledgeEmbeddingAccounts(),
  })
  const accounts = accountsQuery.data || []

  const keysQuery = useQuery({ queryKey: ['mcp-keys'], queryFn: () => api.mcpKeys() })
  const mcpKeys = keysQuery.data || []

  const createMutation = useMutation({
    mutationFn: () =>
      api.createMcpKnowledgeBase({
        name: name.trim(),
        description: description.trim(),
        scope,
        embedding_account_id: accountId === '' ? null : accountId,
        embedding_model: model.trim() || null,
        embedding_dimensions: dimensions ? Number(dimensions) : null,
        allowed_mcp_key_ids: scope === 'restricted' ? allowedKeyIds : [],
      }),
    onSuccess: async (item) => {
      notifyOk('知识库已创建')
      setOpen(false)
      setName('')
      setDescription('')
      setAccountId('')
      setModel('')
      setDimensions('')
      setScope('public')
      setAllowedKeyIds([])
      await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
      navigate(`/mcp-plaza/knowledge/${item.id}`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '创建失败')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteMcpKnowledgeBase(id),
    onSuccess: async () => {
      notifyOk('知识库已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  return (
    <div className="space-y-4">
      <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
        ← 返回服务目录
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">知识库</h2>
          <p className="mt-1 text-sm text-mist">
            每个库可绑定上游账号与向量模型；未绑定则用全局 Fake / env。鉴权走 MCP Key 的 knowledge。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="line" onClick={() => navigate('/mcp-plaza/knowledge/jobs')}>
            <ListChecks size={16} /> 采集任务
          </Button>
          <Button type="button" onClick={() => setOpen(true)}>
            <Plus size={16} /> 新建知识库
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.map((item) => (
          <Card key={item.id} className="min-w-0 space-y-3 overflow-hidden p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-medium text-paper">{item.name}</div>
                <div className="mt-1 break-all text-xs text-mist">{item.id}</div>
              </div>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  if (window.confirm(`删除知识库「${item.name}」？`)) deleteMutation.mutate(item.id)
                }}
              >
                <Trash2 size={15} />
              </Button>
            </div>
            <p className="line-clamp-2 text-sm text-mist">{item.description || '无描述'}</p>
            <div className="break-words text-xs text-mist [overflow-wrap:anywhere]">
              文档 {item.document_count} · 分块 {item.chunk_count}
              {item.embedding_account_name
                ? ` · 向量 ${item.embedding_account_name}${item.embedding_model ? ` / ${item.embedding_model}` : ''}`
                : ' · 向量 未绑定'}
              {item.active_job_count ? ` · 进行中任务 ${item.active_job_count}` : ''}
              {item.stale_document_count ? ` · 待重建 ${item.stale_document_count}` : ''}
              <br />
              范围 {item.scope} · 更新 {formatTime(item.updated_at)}
            </div>
            <Button type="button" variant="line" onClick={() => navigate(`/mcp-plaza/knowledge/${item.id}`)}>
              打开
            </Button>
          </Card>
        ))}
      </div>

      {isError ? (
        <div className="text-sm text-danger">
          {errorMessage(error, '加载失败')}{' '}
          <button type="button" className="underline" onClick={() => void refetch()}>
            重试
          </button>
        </div>
      ) : null}
      {!data.length && !isFetching && !isError ? (
        <div className="rounded-xl border border-dashed border-line px-6 py-16 text-center text-sm text-mist">
          <BookOpen className="mx-auto mb-2" /> 还没有知识库
        </div>
      ) : null}

      {open ? (
        <Dialog title="新建知识库" onClose={() => setOpen(false)}>
          <div className="grid gap-3">
            <Field label="名称">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="产品手册" />
            </Field>
            <Field label="描述">
              <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
            </Field>
            <ScopeFields
              scope={scope}
              allowedKeyIds={allowedKeyIds}
              keys={mcpKeys}
              onScopeChange={setScope}
              onAllowedChange={setAllowedKeyIds}
            />
            <EmbeddingFields
              accounts={accounts}
              accountId={accountId}
              model={model}
              dimensions={dimensions}
              onAccountChange={setAccountId}
              onModelChange={setModel}
              onDimensionsChange={setDimensions}
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                取消
              </Button>
              <Button
                type="button"
                disabled={!name.trim() || createMutation.isPending}
                onClick={() => createMutation.mutate()}
              >
                创建
              </Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}

export function McpKnowledgeDetailPage() {
  const { kbId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [sourceName, setSourceName] = useState('paste.txt')
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('hybrid')
  const [hits, setHits] = useState<Awaited<ReturnType<typeof api.searchMcpKnowledge>> | null>(null)
  const [accountId, setAccountId] = useState<number | ''>('')
  const [model, setModel] = useState('')
  const [dimensions, setDimensions] = useState('')
  const [scope, setScope] = useState('public')
  const [allowedKeyIds, setAllowedKeyIds] = useState<number[]>([])
  const [settingsReady, setSettingsReady] = useState(false)

  const bases = useQuery({ queryKey: ['mcp-knowledge-bases'], queryFn: () => api.mcpKnowledgeBases() })
  const base = bases.data?.find((item) => item.id === kbId)
  const accountsQuery = useQuery({
    queryKey: ['mcp-knowledge-embedding-accounts'],
    queryFn: () => api.mcpKnowledgeEmbeddingAccounts(),
  })
  const accounts = accountsQuery.data || []
  const keysQuery = useQuery({ queryKey: ['mcp-keys'], queryFn: () => api.mcpKeys() })
  const mcpKeys = keysQuery.data || []

  useEffect(() => {
    if (!base || settingsReady) return
    setAccountId(base.embedding_account_id ?? '')
    setModel(base.embedding_model || '')
    setDimensions(base.embedding_dimensions ? String(base.embedding_dimensions) : '')
    setScope(base.scope || 'public')
    setAllowedKeyIds(base.allowed_mcp_key_ids || [])
    setSettingsReady(true)
  }, [base, settingsReady])

  const docs = useQuery({
    queryKey: ['mcp-knowledge-docs', kbId],
    queryFn: () => api.mcpKnowledgeDocuments(kbId),
    enabled: Boolean(kbId),
  })

  const saveSettings = useMutation({
    mutationFn: () =>
      api.updateMcpKnowledgeBase(kbId, {
        scope,
        embedding_account_id: accountId === '' ? null : accountId,
        embedding_model: model.trim() || null,
        embedding_dimensions: dimensions ? Number(dimensions) : null,
        allowed_mcp_key_ids: scope === 'restricted' ? allowedKeyIds : [],
      }),
    onSuccess: async () => {
      notifyOk('设置已保存；若更换了向量模型，请执行「重建向量」')
      await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '保存失败')),
  })

  const reindex = useMutation({
    mutationFn: (onlyStale: boolean) => api.reindexMcpKnowledgeBase(kbId, onlyStale),
    onSuccess: async (result) => {
      notifyOk(`已排队 ${result.created} 个重建任务`)
      await invalidateJobs()
      navigate('/mcp-plaza/knowledge/jobs')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '提交失败')),
  })

  const dirty = useMemo(() => {
    if (!base) return false
    const curAccount = base.embedding_account_id ?? ''
    const curModel = base.embedding_model || ''
    const curDimensions = base.embedding_dimensions ? String(base.embedding_dimensions) : ''
    const curScope = base.scope || 'public'
    const curAllowed = [...(base.allowed_mcp_key_ids || [])].sort().join(',')
    const nextAllowed = [...allowedKeyIds].sort().join(',')
    return (
      curAccount !== accountId ||
      curModel !== (model || '') ||
      curDimensions !== dimensions ||
      curScope !== scope ||
      (scope === 'restricted' && curAllowed !== nextAllowed)
    )
  }, [base, accountId, model, dimensions, scope, allowedKeyIds])

  const invalidateJobs = async () => {
    await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-jobs'] })
    await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
  }

  const submitText = useMutation({
    mutationFn: () =>
      api.createMcpKnowledgeJobText({
        kb_id: kbId,
        text,
        source_name: sourceName || 'paste.txt',
      }),
    onSuccess: async () => {
      notifyOk('任务已提交，可在「采集任务」查看进度')
      setText('')
      await invalidateJobs()
      navigate('/mcp-plaza/knowledge/jobs')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '提交失败')),
  })

  const submitFile = useMutation({
    mutationFn: (file: File) => api.createMcpKnowledgeJobFile(kbId, file),
    onSuccess: async () => {
      notifyOk('文件任务已提交，可在「采集任务」查看进度')
      await invalidateJobs()
      navigate('/mcp-plaza/knowledge/jobs')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '提交失败')),
  })

  const remove = useMutation({
    mutationFn: (docId: string) => api.deleteMcpKnowledgeDocument(kbId, docId),
    onSuccess: async () => {
      notifyOk('文档已删除')
      await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-docs', kbId] })
      await queryClient.invalidateQueries({ queryKey: ['mcp-knowledge-bases'] })
    },
    onError: (caught) => notifyBad(errorMessage(caught, '删除失败')),
  })

  const reembed = useMutation({
    mutationFn: (documentId: string) => api.createMcpKnowledgeReembedJob({ kb_id: kbId, document_id: documentId }),
    onSuccess: async () => {
      notifyOk('重新向量化任务已提交')
      await invalidateJobs()
      navigate('/mcp-plaza/knowledge/jobs')
    },
    onError: (caught) => notifyBad(errorMessage(caught, '提交失败')),
  })

  const search = useMutation({
    mutationFn: () => api.searchMcpKnowledge(kbId, { query, mode, top_k: 5 }),
    onSuccess: (result) => {
      setHits(result)
      if (result.degraded) notifyBad(`已降级全文：${result.degraded_reason || ''}`)
      else notifyOk(`命中 ${result.hits.length} 条`)
    },
    onError: (caught) => notifyBad(errorMessage(caught, '检索失败')),
  })

  return (
    <div className="min-w-0 space-y-5 overflow-x-hidden">
      <Link to="/mcp-plaza/knowledge" className="text-sm text-mist hover:text-paper">
        ← 返回知识库
      </Link>
      <div className="min-w-0">
        <h2 className="text-xl font-semibold">{base?.name || '知识库详情'}</h2>
        <div className="mt-1 break-all text-xs text-mist">{kbId}</div>
      </div>

      <Card className="min-w-0 space-y-3 overflow-hidden p-4">
        <div className="font-medium">访问与向量设置</div>
        <ScopeFields
          scope={scope}
          allowedKeyIds={allowedKeyIds}
          keys={mcpKeys}
          onScopeChange={setScope}
          onAllowedChange={setAllowedKeyIds}
        />
        <EmbeddingFields
          accounts={accounts}
          accountId={accountId}
          model={model}
          dimensions={dimensions}
          onAccountChange={setAccountId}
          onModelChange={setModel}
          onDimensionsChange={setDimensions}
        />
        {base?.stale_document_count ? (
          <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
            有 {base.stale_document_count} 个文档的向量未就绪（配置变更或向量化失败）。
          </div>
        ) : null}
        {base?.signature_mismatch ? (
          <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
            向量库签名与当前 embedding 配置不一致，需全量重建后才能恢复向量检索。
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={!dirty || saveSettings.isPending} onClick={() => saveSettings.mutate()}>
            保存设置
          </Button>
          <Button
            type="button"
            variant="line"
            disabled={reindex.isPending}
            onClick={() => {
              const onlyStale = !base?.signature_mismatch && Boolean(base?.stale_document_count)
              const hint = onlyStale
                ? '为未就绪文档重建向量？'
                : base?.signature_mismatch
                  ? '向量签名不一致，将清空旧向量并全量重建，是否继续？'
                  : '为全部文档重建向量？可能耗时较久。'
              if (window.confirm(hint)) {
                reindex.mutate(onlyStale)
              }
            }}
          >
            重建向量
          </Button>
        </div>
      </Card>

      <Card className="grid min-w-0 gap-3 overflow-hidden p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="font-medium">提交采集任务</div>
          <Link to="/mcp-plaza/knowledge/jobs" className="text-sm text-signal hover:underline">
            查看采集任务 →
          </Link>
        </div>
        <Field label="粘贴纯文本入库">
          <textarea
            className="min-h-28 w-full rounded-md border border-line bg-ink px-3 py-2 text-sm text-paper"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="支持 .txt / .md 内容"
          />
        </Field>
        <Field label="来源名">
          <Input value={sourceName} onChange={(e) => setSourceName(e.target.value)} />
        </Field>
        <Button
          type="button"
          disabled={!text.trim() || submitText.isPending || submitFile.isPending}
          onClick={() => submitText.mutate()}
        >
          提交入库任务
        </Button>

        <div className="border-t border-line pt-3">
          <Field label="或上传文本文件（.txt / .md）">
            <input
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              disabled={submitFile.isPending}
              className="block w-full min-w-0 text-sm text-mist file:mr-3 file:rounded-md file:border file:border-line file:bg-panel file:px-3 file:py-1.5 file:text-sm file:text-paper"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) submitFile.mutate(file)
                e.target.value = ''
              }}
            />
          </Field>
        </div>
        <p className="text-xs text-mist">
          任务提交后在后台队列执行，可离开页面；在「采集任务」查看进度、失败重试。大文本处理较慢，单文档上限 2 MiB。
        </p>
      </Card>

      <Card className="grid min-w-0 gap-3 overflow-hidden p-4">
        <div className="font-medium">试检索</div>
        <Field label="查询">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="关键词或语义问题" />
        </Field>
        <Field label="模式">
          <select
            className="w-full rounded-md border border-line bg-ink px-3 py-2 text-sm"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="hybrid">hybrid</option>
            <option value="fulltext">fulltext</option>
            <option value="vector">vector</option>
          </select>
        </Field>
        <Button type="button" variant="line" disabled={!query.trim() || search.isPending} onClick={() => search.mutate()}>
          检索
        </Button>
        {hits ? (
          <div className="min-w-0 space-y-2">
            {hits.degraded ? <div className="text-xs text-warn">degraded: {hits.degraded_reason}</div> : null}
            {hits.hits.map((hit) => (
              <div key={hit.chunk_id} className="min-w-0 rounded-md border border-line p-3 text-sm">
                <div className="text-xs text-mist">
                  score {hit.score.toFixed(3)} · {hit.source_name}
                </div>
                <div className="mt-1 whitespace-pre-wrap break-words text-paper [overflow-wrap:anywhere]">{hit.text}</div>
              </div>
            ))}
            {!hits.hits.length ? <div className="text-sm text-mist">无命中</div> : null}
          </div>
        ) : null}
      </Card>

      <div className="space-y-2">
        <div className="font-medium">文档</div>
        {(docs.data || []).map((doc) => (
          <Card key={doc.id} className="min-w-0 space-y-2 overflow-hidden p-3">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="break-all text-sm text-paper">{doc.source_name}</div>
                <div className="break-words text-xs text-mist [overflow-wrap:anywhere]">
                  {doc.chunk_count} chunks · {doc.content_size} B · 向量 {doc.vector_status}
                  {doc.vector_error ? ` · ${doc.vector_error}` : ''}
                </div>
              </div>
              <div className="flex gap-2">
                <Button type="button" variant="line" disabled={reembed.isPending} onClick={() => reembed.mutate(doc.id)}>
                  重新向量化
                </Button>
                <Button type="button" variant="ghost" onClick={() => remove.mutate(doc.id)}>
                  删除
                </Button>
              </div>
            </div>
          </Card>
        ))}
        {!docs.data?.length ? <div className="text-sm text-mist">暂无文档</div> : null}
      </div>
    </div>
  )
}

export default McpKnowledgePage
