import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, CheckCircle2, CircleStop, Download, Gauge, Info, Play, RotateCcw, Search, Settings2, SlidersHorizontal, XCircle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { BenchmarkGroupSelectDialog } from '../components/BenchmarkGroupSelectDialog'
import { BenchmarkGroupsDialog } from '../components/BenchmarkGroupsDialog'
import { BenchmarkModelPicker } from '../components/BenchmarkModelPicker'
import { Badge, Button, Card, Dialog, Field, Input } from '../components/ui'
import { api, type BenchmarkGroupTarget, type BenchmarkResult } from '../lib/api'
import { keyOf, targetsFromAccounts, type Target } from '../lib/benchmark'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage, shanghaiDateStamp } from '../lib/utils'
import { useCan } from '../lib/session'

function formatMs(value?: number) {
  return value ? `${value} ms` : '—'
}

function formatSpeed(value?: number) {
  return value ? `${value} tok/s` : '—'
}

function previewText(result?: BenchmarkResult) {
  if (!result) return '尚未测试'
  return result.preview || result.error || (result.ok ? '已返回，但未提取到文本' : '尚未测试')
}

function SpeedHint() {
  return (
    <span className="inline-flex items-center gap-1">
      输出速度
      <button type="button" className="group relative inline-flex" aria-label="输出速度计算公式">
        <Info size={13} className="cursor-help text-mist group-hover:text-paper group-focus-visible:text-paper" />
        <span className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-72 border border-line bg-panel-2 p-3 text-left text-[11px] leading-5 font-normal text-mist shadow-[0_12px_32px_rgba(0,0,0,0.45)] group-hover:block group-focus-visible:block">
          <span className="block font-mono text-paper">tok/s = completion_tokens ÷ ((总耗时 − 首 token) / 1000)</span>
          <span className="mt-1.5 block">分子取上游 usage 的 completion_tokens（含思考，不再另加 reasoning_tokens）。分母为首 token 之后的生成窗口。无 usage 时不显示速度。</span>
        </span>
      </button>
    </span>
  )
}

function ResultStatus({ result, isTesting }: { result?: BenchmarkResult; isTesting: boolean }) {
  const className = 'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap'
  if (isTesting) return <span className={cn(className, 'text-signal')}>测试中</span>
  if (!result) return <span className={cn(className, 'text-mist')}>尚未测试</span>
  if (result.timeout) return <span className={cn(className, 'text-warn')}>超时</span>
  if (result.ok) {
    return (
      <span className={cn(className, 'text-signal')}>
        <CheckCircle2 size={16} className="shrink-0" />
        成功
      </span>
    )
  }
  return (
    <span className={cn(className, 'text-danger')} title={result.error}>
      <XCircle size={16} className="shrink-0" />
      失败
    </span>
  )
}

function ResultBadge({ result, isTesting }: { result?: BenchmarkResult; isTesting: boolean }) {
  if (isTesting) return <Badge tone="info">测试中</Badge>
  if (!result) return <Badge>尚未测试</Badge>
  if (result.timeout) return <Badge tone="warn">超时</Badge>
  if (result.ok) return <Badge tone="ok">成功</Badge>
  return (
    <Badge tone="bad" title={result.error}>
      失败
    </Badge>
  )
}

export function BenchmarkPage() {
  // 进得来页面即具备测速能力（页面入口权限 = benchmark:view）
  const canRun = useCan('benchmark:view')
  const canManageGroups = useCan('benchmark_groups:manage')
  const canExport = useCan('benchmark_history:export')
  const canManageModels = useCan('accounts:models_manage')
  const queryClient = useQueryClient()
  const { data: accounts = [], isLoading } = useQuery({ queryKey: ['key-accounts'], queryFn: api.keyAccounts })
  const { data: groupsData } = useQuery({ queryKey: ['benchmark-groups'], queryFn: api.benchmarkGroups })
  const groups = groupsData?.items ?? []
  const [prompt, setPrompt] = useState('用一句话介绍你自己。')
  const [maxTokens, setMaxTokens] = useState('512')
  const [configOpen, setConfigOpen] = useState(false)
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [groupDialogOpen, setGroupDialogOpen] = useState(false)
  const [groupSelectOpen, setGroupSelectOpen] = useState(false)
  const [activeGroupId, setActiveGroupId] = useState<number | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [results, setResults] = useState<Record<string, BenchmarkResult>>({})
  const [testing, setTesting] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const stopRef = useRef(false)
  const targets = useMemo<Target[]>(() => targetsFromAccounts(accounts), [accounts])
  const selectedTargets = targets.filter((target) => selected.includes(keyOf(target)))
  const visibleTargets = selectedTargets
  const visibleResults = visibleTargets.map((target) => results[keyOf(target)]).filter((result): result is BenchmarkResult => Boolean(result))
  const orderedResults = visibleResults.sort((left, right) => (left.first_token_ms ?? Infinity) - (right.first_token_ms ?? Infinity))
  const completedCount = visibleTargets.filter((target) => results[keyOf(target)]).length
  const failedAnyCount = visibleTargets.filter((target) => {
    const result = results[keyOf(target)]
    return Boolean(result && !result.ok)
  }).length
  const failedEnabledTargets = visibleTargets.filter((target) => {
    const result = results[keyOf(target)]
    return Boolean(result && !result.ok && target.enabled && !testing.includes(keyOf(target)))
  })

  function toggle(target: Target) {
    const key = keyOf(target)
    setSelected((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }
  function clearSelected() { setSelected([]) }
  const currentTargets: BenchmarkGroupTarget[] = selectedTargets.map((target) => ({ account_id: target.account.id, model: target.model }))
  function applyGroup(groupId: number) {
    const group = groups.find((item) => item.id === groupId)
    if (!group) return
    const targetKeys = new Set(targets.map(keyOf))
    const next = group.members
      .filter((member) => member.available)
      .map((member) => `${member.account_id}:${member.model}`)
      .filter((key) => targetKeys.has(key))
    setSelected(next)
    setActiveGroupId(groupId)
    setGroupSelectOpen(false)
    const skipped = group.member_count - next.length
    if (skipped > 0) notifyBad(`已应用「${group.name}」，跳过 ${skipped} 个不可用目标`)
    else notifyOk(`已应用「${group.name}」`)
  }
  async function saveGroup(name: string) {
    if (!name || !currentTargets.length) return
    try {
      await api.createBenchmarkGroup({ name, targets: currentTargets })
      await queryClient.invalidateQueries({ queryKey: ['benchmark-groups'] })
      notifyOk(`已保存分组「${name}」`)
    } catch (error) {
      notifyBad(errorMessage(error, '保存分组失败'))
    }
  }
  async function runTargets(list: Target[]) {
    if (!list.length) return
    stopRef.current = false
    setRunning(true)
    const collected: BenchmarkResult[] = []
    for (const target of list) {
      if (stopRef.current) break
      const targetKey = keyOf(target)
      setTesting((current) => current.includes(targetKey) ? current : [...current, targetKey])
      try {
        const result = await api.benchmark({ account_id: target.account.id, model: target.model, prompt, max_tokens: Number(maxTokens) || 64 })
        collected.push(result)
        setResults((current) => ({ ...current, [targetKey]: result }))
      } catch (error) {
        const message = errorMessage(error, '测速请求失败')
        notifyBad(message)
        const failure: BenchmarkResult = {
          ok: false,
          account_id: target.account.id,
          account_name: target.account.name,
          provider: target.account.provider,
          model: target.model,
          error: message.slice(0, 500),
        }
        collected.push(failure)
        setResults((current) => ({ ...current, [targetKey]: failure }))
      } finally {
        setTesting((current) => current.filter((item) => item !== targetKey))
      }
    }
    setRunning(false)
    await persistRun(collected)
  }
  async function persistRun(collected: BenchmarkResult[]) {
    if (!collected.length) return
    try {
      await api.saveBenchmarkRun({ prompt, max_tokens: Number(maxTokens) || 64, group_id: activeGroupId ?? undefined, results: collected })
      await queryClient.invalidateQueries({ queryKey: ['benchmark-history'] })
      await queryClient.invalidateQueries({ queryKey: ['benchmark-leaderboard'] })
      notifyOk(`已自动保存 ${collected.length} 条测速结果`)
    } catch (error) {
      notifyBad(errorMessage(error, '自动保存测速结果失败'))
    }
  }
  function run() { void runTargets(visibleTargets) }
  function retryFailed() {
    void runTargets(visibleTargets.filter((target) => {
      const result = results[keyOf(target)]
      return Boolean(result && !result.ok)
    }))
  }
  function stop() { stopRef.current = true; setRunning(false) }
  async function refreshAfterDisable() {
    await queryClient.invalidateQueries({ queryKey: ['key-accounts'] })
    await queryClient.invalidateQueries({ queryKey: ['accounts'] })
    await queryClient.invalidateQueries({ queryKey: ['agents'] })
    await queryClient.invalidateQueries({ queryKey: ['agent'] })
  }
  async function disableTarget(target: Target) {
    try {
      await api.updateAccountModel(target.account.id, target.model, { enabled: false })
      await refreshAfterDisable()
      notifyOk(`已关闭 ${target.model}`)
    } catch (error) {
      notifyBad(errorMessage(error, '关闭模型失败'))
    }
  }
  async function disableFailedTargets() {
    if (!failedEnabledTargets.length) return
    const failed = failedEnabledTargets
    const errors: string[] = []
    for (const target of failed) {
      try {
        await api.updateAccountModel(target.account.id, target.model, { enabled: false })
      } catch (error) {
        errors.push(`${target.model}: ${errorMessage(error, '关闭失败')}`)
      }
    }
    await refreshAfterDisable()
    if (errors.length) notifyBad(`有 ${errors.length} 个模型关闭失败`)
    else notifyOk(`已关闭 ${failed.length} 个失败或超时模型`)
  }
  async function exportResults() {
    try {
      const blob = await api.exportBenchmarkHistory()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `benchmark-history-${shanghaiDateStamp()}.csv`
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) { notifyBad(errorMessage(error, '导出测速结果失败')) }
  }

  return (
    <div className="flex min-w-0 w-full flex-col xl:h-full xl:min-h-0">
      <div className="mb-4 flex shrink-0 flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex items-start justify-between gap-3 lg:block lg:min-w-0 lg:flex-1">
          <div className="min-w-0">
            <div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK LAB / LIVE</div>
            <h1 className="text-2xl font-semibold tracking-tight">模型测速</h1>
            <button
              type="button"
              className="mt-2 flex max-w-2xl items-center gap-2 text-left text-sm text-mist hover:text-paper"
              onClick={() => setConfigOpen(true)}
              title={prompt}
            >
              <SlidersHorizontal size={14} className="shrink-0 text-signal" />
              <span className="min-w-0 truncate">提示词：{prompt} · 最大 {maxTokens} tokens</span>
            </button>
          </div>
          {running ? (
            <Button type="button" variant="danger" className="shrink-0 lg:hidden" onClick={stop}>
              <CircleStop size={16} />
              停止
            </Button>
          ) : canRun ? (
            <Button type="button" className="shrink-0 lg:hidden" onClick={() => void run()} disabled={isLoading || !visibleTargets.length}>
              <Play size={16} />
              开始
            </Button>
          ) : null}
        </div>
        <div className="grid grid-cols-2 items-end gap-2 lg:flex lg:shrink-0 lg:flex-wrap lg:justify-end">
{canManageGroups ? (
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setConfigOpen(true)}>
            <SlidersHorizontal size={16} />
            测试配置
          </Button>
) : null}
{canManageGroups ? (
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setGroupDialogOpen(true)}>
            <Settings2 size={16} />
            分组管理
          </Button>
) : null}
{canExport ? (
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => void exportResults()}>
            <Download size={16} />
            导出历史
          </Button>
) : null}
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setResults({})}>
            <RotateCcw size={16} />
            清空结果
          </Button>
          {running ? (
            <Button type="button" variant="danger" className="hidden lg:inline-flex" onClick={stop}>
              <CircleStop size={16} />
              停止
            </Button>
          ) : canRun ? (
            <Button type="button" className="hidden lg:inline-flex" onClick={() => void run()} disabled={isLoading || !visibleTargets.length}>
              <Play size={16} />
              开始测速
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[minmax(340px,0.8fr)_minmax(0,1.8fr)]">
        <section className="flex min-h-0 flex-col overflow-hidden border border-line bg-panel/60 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 font-semibold">测速目标 <Gauge size={18} className="text-signal" /></h2>
            <span className="font-mono text-xs text-signal">{selectedTargets.length} 已选</span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-mist">共 {targets.length} 个可用模型</span>
            <div className="flex items-center gap-2">
              <Button variant="line" className="px-3" onClick={() => setGroupSelectOpen(true)}>
                <Settings2 size={14} />
                选择分组
              </Button>
              <Button variant="line" className="px-3" onClick={() => setSelectorOpen(true)}>
                <Search size={14} />
                选择模型
              </Button>
            </div>
          </div>
          {!isLoading && !targets.length ? <div className="mt-2 text-xs text-mist">暂无已同步模型，请先在账号页刷新模型。</div> : null}
          <div className="mt-4 flex min-h-0 flex-1 flex-col">
            <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-mist">
              <span>已选测试目标</span>
              <button type="button" className="shrink-0 hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={clearSelected} disabled={!selected.length}>
                清空已选
              </button>
            </div>
            <div className="max-h-72 min-h-0 flex-1 overflow-y-auto pr-1 xl:max-h-none">
              <div className="grid gap-1.5">
                {selectedTargets.map((target) => (
                  <div key={keyOf(target)} className="flex min-w-0 items-center gap-2 border border-signal/30 bg-signal/10 px-3 py-2 text-xs">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-paper" title={target.model}>{target.model}</span>
                      <span className="mt-0.5 block truncate text-[10px] text-mist" title={target.account.name}>
                        {target.account.name} · {target.account.provider}
                        {target.enabled ? '' : ' · 已关闭'}
                      </span>
                    </span>
                    <button type="button" className="shrink-0 text-mist hover:text-danger" onClick={() => toggle(target)} aria-label={`移除 ${target.model}`}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
              {!selectedTargets.length ? (
                <div className="py-8 text-center text-xs text-mist">
                  还没有选择模型
                  <br />
                  点击“选择模型”开始配置
                </div>
              ) : null}
            </div>
          </div>
        </section>

        <section className="flex min-w-0 flex-col xl:min-h-0 xl:flex-1">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-semibold">测速结果</h2>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-mist">{completedCount} / {visibleTargets.length} 已完成</span>
              {canRun ? (
              <Button
                type="button"
                variant="line"
                className="px-3"
                disabled={running || !failedAnyCount}
                onClick={retryFailed}
              >
                <RotateCcw size={14} />
                重试失败
              </Button>
              ) : null}
              {canManageModels ? (
              <Button
                type="button"
                variant="danger"
                className="px-3"
                disabled={running || !failedEnabledTargets.length}
                onClick={() => void disableFailedTargets()}
              >
                <Ban size={14} />
                一键关闭
              </Button>
              ) : null}
            </div>
          </div>
          {visibleTargets.length > 0 && completedCount > 0 && completedCount < visibleTargets.length ? (
            <div className="mb-3 h-1 w-full overflow-hidden rounded-full bg-white/5">
              <div className="h-full bg-signal transition-all" style={{ width: `${(completedCount / visibleTargets.length) * 100}%` }} />
            </div>
          ) : null}

          <div className="hidden min-h-0 overflow-auto border border-line bg-panel/40 lg:block xl:flex-1">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="sticky top-0 z-10 border-b border-line bg-panel text-xs text-mist">
                <tr>
                  <th className="px-4 py-3">账号 / 模型</th>
                  <th className="w-28 px-4 py-3">状态</th>
                  <th className="px-4 py-3">首 token</th>
                  <th className="px-4 py-3">总耗时</th>
                  <th className="px-4 py-3"><SpeedHint /></th>
                  <th className="px-4 py-3">响应预览</th>
                </tr>
              </thead>
              <tbody>
                {visibleTargets.map((target) => {
                  const targetKey = keyOf(target)
                  const result = results[targetKey]
                  const isTesting = testing.includes(targetKey)
                  return (
                    <tr key={targetKey} className="border-b border-line/60 last:border-0">
                      <td className="px-4 py-4">
                        <div className="font-medium">{target.model}</div>
                        <div className="mt-1 text-xs text-mist">
                          {target.account.name} · {target.account.provider}
                          {target.enabled ? '' : ' · 已关闭'}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-4">
                        <ResultStatus result={result} isTesting={isTesting} />
                      </td>
                      <td className="px-4 py-4 font-mono">{formatMs(result?.first_token_ms)}</td>
                      <td className="px-4 py-4 font-mono">{formatMs(result?.total_ms)}</td>
                      <td className="px-4 py-4 font-mono">{formatSpeed(result?.output_tokens_per_second)}</td>
                      <td className="max-w-[260px] px-4 py-4 text-xs text-mist">
                        <div>{previewText(result)}</div>
                        {canManageModels && result && !result.ok && target.enabled && !isTesting ? (
                          <button type="button" className="mt-2 text-danger hover:underline" onClick={() => void disableTarget(target)}>
                            关闭此模型
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
                {!visibleTargets.length ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-12 text-center text-sm text-mist">选择模型后开始测速</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div className="grid gap-3 lg:hidden">
            {visibleTargets.map((target) => {
              const targetKey = keyOf(target)
              const result = results[targetKey]
              const isTesting = testing.includes(targetKey)
              const preview = previewText(result)
              return (
                <Card key={targetKey} className="min-w-0 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-medium" title={target.model}>{target.model}</div>
                      <div className="mt-0.5 truncate text-xs text-mist" title={target.account.name}>
                        {target.account.name} · {target.account.provider}
                        {target.enabled ? '' : ' · 已关闭'}
                      </div>
                    </div>
                    <span className="shrink-0">
                      <ResultBadge result={result} isTesting={isTesting} />
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                      <div className="text-xs uppercase tracking-[0.16em] text-mist">首 token</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatMs(result?.first_token_ms)}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                      <div className="text-xs uppercase tracking-[0.16em] text-mist">总耗时</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatMs(result?.total_ms)}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                      <div className="text-xs uppercase tracking-[0.16em] text-mist">速度</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatSpeed(result?.output_tokens_per_second)}</div>
                    </div>
                  </div>
                  <div className={cn('break-all text-xs [overflow-wrap:anywhere]', result?.error ? 'text-danger' : 'text-mist')} title={preview}>
                    {preview}
                  </div>
                  {canManageModels && result && !result.ok && target.enabled && !isTesting ? (
                    <Button type="button" variant="danger" className="w-full" onClick={() => void disableTarget(target)}>
                      关闭此模型
                    </Button>
                  ) : null}
                </Card>
              )
            })}
            {!visibleTargets.length ? (
              <Card>
                <div className="px-2 py-8 text-center text-sm text-mist">选择模型后开始测速</div>
              </Card>
            ) : null}
          </div>

          {orderedResults.length ? (
            <div className="mt-5 grid grid-cols-3 gap-2 lg:gap-3">
              {[
                ['最快首 token', `${orderedResults[0].first_token_ms ?? 0} ms`],
                ['最快完整响应', `${Math.min(...orderedResults.map((item) => item.total_ms ?? Infinity))} ms`],
                ['最高输出速度', `${Math.max(...orderedResults.map((item) => item.output_tokens_per_second ?? 0))} tok/s`],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0 border border-line bg-panel/40 p-3 lg:p-4">
                  <div className="truncate text-[10px] text-mist lg:text-xs">{label}</div>
                  <div className="mt-2 truncate font-mono text-sm text-signal lg:text-xl">{value}</div>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      {configOpen ? (
        <Dialog title="测速配置" onClose={() => setConfigOpen(false)}>
          <div className="space-y-4">
            <Field label="提示词">
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                className="min-h-28 w-full resize-y border border-line-strong bg-black/20 p-3 text-sm text-paper outline-none focus:border-signal focus:ring-2 focus:ring-signal/20"
              />
            </Field>
            <Field label="最大输出 token">
              <Input type="number" min="1" max="512" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} />
            </Field>
            <p className="text-xs text-mist">最大输出 token 上限 512。</p>
            <div className="flex justify-end">
              <Button onClick={() => setConfigOpen(false)}>完成</Button>
            </div>
          </div>
        </Dialog>
      ) : null}

      {groupDialogOpen ? <BenchmarkGroupsDialog onClose={() => setGroupDialogOpen(false)} /> : null}

      {groupSelectOpen ? (
        <BenchmarkGroupSelectDialog
          groups={groups}
          currentTargetCount={currentTargets.length}
          activeGroupId={activeGroupId}
          onApply={applyGroup}
          onSaveNew={(name) => void saveGroup(name)}
          onManage={() => {
            setGroupSelectOpen(false)
            setGroupDialogOpen(true)
          }}
          canManage={canManageGroups}
          onClose={() => setGroupSelectOpen(false)}
        />
      ) : null}

      {selectorOpen ? (
        <BenchmarkModelPicker
          targets={targets}
          selected={selected}
          onChange={setSelected}
          onClose={() => setSelectorOpen(false)}
        />
      ) : null}
    </div>
  )
}
