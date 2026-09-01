import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, CheckCircle2, CircleStop, Download, Gauge, Info, PanelLeftClose, PanelLeftOpen, Play, RotateCcw, Save, Search, XCircle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { Badge, Button, Card, Dialog, Field, Input, Select } from '../components/ui'
import { api, type Account, type BenchmarkResult } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'

type Target = { account: Account; model: string; enabled: boolean }

function keyOf(target: Target) {
  return `${target.account.id}:${target.model}`
}

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
  const queryClient = useQueryClient()
  const { data: accounts = [], isLoading } = useQuery({ queryKey: ['benchmark-accounts'], queryFn: api.keyAccounts })
  const [prompt, setPrompt] = useState('用一句话介绍你自己。')
  const [maxTokens, setMaxTokens] = useState('256')
  const [sourceFilter, setSourceFilter] = useState<'all' | 'upstream' | 'agent'>('all')
  const [providerFilter, setProviderFilter] = useState('all')
  const [accountFilter, setAccountFilter] = useState('all')
  const [targetSearch, setTargetSearch] = useState('')
  const [selectorOpen, setSelectorOpen] = useState(false)
  const [configCollapsed, setConfigCollapsed] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [results, setResults] = useState<Record<string, BenchmarkResult>>({})
  const [testing, setTesting] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const stopRef = useRef(false)
  const providers = useMemo(() => [...new Set(accounts.map((account) => account.provider))], [accounts])
  const filteredAccounts = useMemo(
    () => accounts.filter((account) =>
      account.status === 'active'
      && (sourceFilter === 'all' || account.source === sourceFilter)
      && (providerFilter === 'all' || account.provider === providerFilter),
    ),
    [accounts, providerFilter, sourceFilter],
  )
  const targets = useMemo<Target[]>(
    () => filteredAccounts.filter((account) => accountFilter === 'all' || String(account.id) === accountFilter)
      .flatMap((account) => account.models.map((model) => ({ account, model: model.id, enabled: model.enabled !== false }))),
    [accountFilter, filteredAccounts],
  )
  const searchedTargets = useMemo(() => {
    const query = targetSearch.trim().toLowerCase()
    if (!query) return targets
    return targets.filter((target) => [target.model, target.account.name, target.account.provider].some((value) => value.toLowerCase().includes(query)))
  }, [targetSearch, targets])
  const selectedTargets = targets.filter((target) => selected.includes(keyOf(target)))
  const visibleTargets = targets.filter((target) => selected.includes(keyOf(target)))
  const visibleResults = visibleTargets.map((target) => results[keyOf(target)]).filter((result): result is BenchmarkResult => Boolean(result))
  const orderedResults = visibleResults.sort((left, right) => (left.first_token_ms ?? Infinity) - (right.first_token_ms ?? Infinity))
  const failedEnabledTargets = visibleTargets.filter((target) => {
    const result = results[keyOf(target)]
    return Boolean(result && !result.ok && target.enabled && !testing.includes(keyOf(target)))
  })

  function toggle(target: Target) {
    const key = keyOf(target)
    setSelected((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }
  function selectAll() {
    const searchedKeys = searchedTargets.map(keyOf)
    const allSearchedSelected = searchedKeys.length > 0 && searchedKeys.every((key) => selected.includes(key))
    setSelected((current) => allSearchedSelected ? current.filter((key) => !searchedKeys.includes(key)) : [...new Set([...current, ...searchedKeys])])
  }
  function clearSelected() { setSelected([]) }
  async function run() {
    if (!visibleTargets.length) return
    stopRef.current = false
    setRunning(true)
    for (const target of visibleTargets) {
      if (stopRef.current) break
      const targetKey = keyOf(target)
      setTesting((current) => current.includes(targetKey) ? current : [...current, targetKey])
      try {
        const result = await api.benchmark({ account_id: target.account.id, model: target.model, prompt, max_tokens: Number(maxTokens) || 64 })
        setResults((current) => ({ ...current, [targetKey]: result }))
      } catch (error) { notifyBad(errorMessage(error, '测速请求失败')) }
      finally { setTesting((current) => current.filter((item) => item !== targetKey)) }
    }
    setRunning(false)
  }
  function stop() { stopRef.current = true; setRunning(false) }
  async function refreshAfterDisable() {
    await queryClient.invalidateQueries({ queryKey: ['benchmark-accounts'] })
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
  async function saveResults() {
    if (!visibleResults.length) return
    try {
      await api.saveBenchmarkRun({ prompt, max_tokens: Number(maxTokens) || 64, results: visibleResults })
      notifyOk('测速结果已保存')
    } catch (error) { notifyBad(errorMessage(error, '保存测速结果失败')) }
  }
  async function exportResults() {
    try {
      const blob = await api.exportBenchmarkHistory()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `benchmark-history-${new Date().toISOString().slice(0, 10)}.csv`
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
            <p className="mt-2 max-w-2xl text-sm text-mist">用同一条提示词测量真实链路。</p>
          </div>
          {running ? (
            <Button type="button" variant="danger" className="shrink-0 lg:hidden" onClick={stop}>
              <CircleStop size={16} />
              停止
            </Button>
          ) : (
            <Button type="button" className="shrink-0 lg:hidden" onClick={() => void run()} disabled={isLoading || !visibleTargets.length}>
              <Play size={16} />
              开始
            </Button>
          )}
        </div>
        <div className="grid grid-cols-2 items-end gap-2 lg:flex lg:shrink-0 lg:flex-nowrap">
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => void exportResults()}>
            <Download size={16} />
            导出历史
          </Button>
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => void saveResults()} disabled={running || !visibleResults.length}>
            <Save size={16} />
            保存本次
          </Button>
          <Button type="button" variant="line" className="w-full lg:w-auto" onClick={() => setResults({})}>
            <RotateCcw size={16} />
            清空结果
          </Button>
          {running ? (
            <Button type="button" variant="danger" className="hidden lg:inline-flex" onClick={stop}>
              <CircleStop size={16} />
              停止
            </Button>
          ) : (
            <Button type="button" className="hidden lg:inline-flex" onClick={() => void run()} disabled={isLoading || !visibleTargets.length}>
              <Play size={16} />
              开始测速
            </Button>
          )}
        </div>
      </div>

      <div className={cn('grid gap-6 xl:min-h-0 xl:flex-1', configCollapsed ? 'xl:grid-cols-[56px_minmax(0,1fr)]' : 'xl:grid-cols-[minmax(390px,0.82fr)_minmax(0,1.8fr)]')}>
        <section className={cn('flex flex-col overflow-hidden border border-line bg-panel/60 p-5 xl:min-h-0', configCollapsed && 'xl:items-center xl:p-2')}>
          <div className={cn('flex items-center', configCollapsed ? 'mb-5 justify-between xl:mb-0 xl:flex-col xl:gap-3' : 'mb-5 justify-between')}>
            <div className={cn('flex items-center gap-2', configCollapsed && 'xl:hidden')}>
              <h2 className="font-semibold">测试配置</h2>
              <Gauge size={19} className="text-signal" />
            </div>
            {configCollapsed ? <Gauge size={19} className="hidden text-signal xl:block" /> : null}
            <button
              type="button"
              className="hidden text-mist hover:text-paper xl:inline-flex"
              onClick={() => setConfigCollapsed((current) => !current)}
              aria-label={configCollapsed ? '展开测试配置' : '折叠测试配置'}
              title={configCollapsed ? '展开测试配置' : '折叠测试配置'}
            >
              {configCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            </button>
          </div>
          <div className={cn('grid min-h-0 flex-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(170px,0.75fr)]', configCollapsed && 'xl:hidden')}>
            <div className="grid min-h-0 content-start gap-4">
              <Field label="提示词">
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  className="min-h-24 w-full resize-y border border-line bg-black/20 p-3 text-sm text-paper outline-none focus:border-signal"
                />
              </Field>
              <Field label="最大输出 token">
                <Input type="number" min="1" max="512" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="账号来源">
                  <Select
                    value={sourceFilter}
                    onChange={(event) => {
                      setSourceFilter(event.target.value as typeof sourceFilter)
                      setAccountFilter('all')
                    }}
                  >
                    <option value="all">全部来源</option>
                    <option value="upstream">上游账号</option>
                    <option value="agent">网关代理</option>
                  </Select>
                </Field>
                <Field label="供应商筛选">
                  <Select
                    value={providerFilter}
                    onChange={(event) => {
                      setProviderFilter(event.target.value)
                      setAccountFilter('all')
                    }}
                  >
                    <option value="all">全部供应商</option>
                    {providers.map((provider) => (
                      <option key={provider} value={provider}>{provider}</option>
                    ))}
                  </Select>
                </Field>
              </div>
              <Field label="具体账号">
                <Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}>
                  <option value="all">全部账号</option>
                  {filteredAccounts.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name} · {account.source === 'agent' ? '网关代理' : '上游'}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="border-t border-line pt-4">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-semibold">选择测试模型</span>
                  <span className="font-mono text-xs text-signal">{selectedTargets.length} 已选</span>
                </div>
                <div className="mt-3 flex items-center justify-between gap-3 text-[11px] text-mist">
                  <span>共 {targets.length} 个可用模型</span>
                  <Button variant="line" className="px-3" onClick={() => setSelectorOpen(true)}>
                    <Search size={14} />
                    选择模型
                  </Button>
                </div>
              </div>
              {!isLoading && !targets.length ? <div className="text-xs text-mist">暂无已同步模型，请先在账号页刷新模型。</div> : null}
            </div>
            <div className="flex min-h-0 flex-col border-t border-line pt-4 xl:border-l xl:border-t-0 xl:pl-4 xl:pt-0">
              <div className="mb-3 flex items-center justify-between gap-2 text-[11px] text-mist">
                <span>已选测试目标</span>
                <button type="button" className="shrink-0 hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={clearSelected} disabled={!selected.length}>
                  清空已选
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto pr-1">
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
          </div>
        </section>

        <section className="flex min-w-0 flex-col xl:min-h-0 xl:flex-1">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-semibold">测速结果</h2>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-mist">{orderedResults.length} / {visibleTargets.length} completed</span>
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
            </div>
          </div>

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
                        {result && !result.ok && target.enabled && !isTesting ? (
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
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">首 token</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatMs(result?.first_token_ms)}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">总耗时</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatMs(result?.total_ms)}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-ink/40 px-2 py-2">
                      <div className="text-[10px] uppercase tracking-[0.16em] text-mist">速度</div>
                      <div className="mt-1 truncate font-mono text-xs tabular-nums">{formatSpeed(result?.output_tokens_per_second)}</div>
                    </div>
                  </div>
                  <div className={cn('break-all text-xs [overflow-wrap:anywhere]', result?.error ? 'text-danger' : 'text-mist')} title={preview}>
                    {preview}
                  </div>
                  {result && !result.ok && target.enabled && !isTesting ? (
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

      {selectorOpen ? (
        <Dialog title="选择测试模型" className="my-6 max-w-5xl lg:my-[15vh]" onClose={() => setSelectorOpen(false)}>
          <div className="flex max-h-[min(680px,calc(100vh-10rem))] flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="账号来源">
                <Select
                  value={sourceFilter}
                  onChange={(event) => {
                    setSourceFilter(event.target.value as typeof sourceFilter)
                    setAccountFilter('all')
                  }}
                >
                  <option value="all">全部来源</option>
                  <option value="upstream">上游账号</option>
                  <option value="agent">网关代理</option>
                </Select>
              </Field>
              <Field label="供应商筛选">
                <Select
                  value={providerFilter}
                  onChange={(event) => {
                    setProviderFilter(event.target.value)
                    setAccountFilter('all')
                  }}
                >
                  <option value="all">全部供应商</option>
                  {providers.map((provider) => (
                    <option key={provider} value={provider}>{provider}</option>
                  ))}
                </Select>
              </Field>
            </div>
            <Field label="具体账号">
              <Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}>
                <option value="all">全部账号</option>
                {filteredAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.source === 'agent' ? '网关代理' : '上游'}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="relative">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
              <Input value={targetSearch} onChange={(event) => setTargetSearch(event.target.value)} placeholder="搜索模型、账号或供应商" className="pl-9" />
            </div>
            <div className="flex items-center justify-between gap-3 text-xs text-mist">
              <span>搜索结果 {searchedTargets.length} 个</span>
              <div className="flex gap-3">
                <button type="button" className="text-signal hover:underline" onClick={selectAll}>
                  {searchedTargets.length > 0 && searchedTargets.every((target) => selected.includes(keyOf(target))) ? '取消全选' : '全选搜索结果'}
                </button>
                <button type="button" className="hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={clearSelected} disabled={!selected.length}>
                  清空已选
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto border-y border-line py-2">
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {searchedTargets.map((target) => {
                  const key = keyOf(target)
                  const checked = selected.includes(key)
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => toggle(target)}
                      className={cn(
                        'flex w-full min-w-0 items-center gap-3 border px-3 py-2.5 text-left text-sm transition-colors',
                        checked ? 'border-signal/50 bg-signal/10' : 'border-transparent hover:border-line hover:bg-white/5',
                      )}
                    >
                      <span className={cn('flex h-4 w-4 shrink-0 items-center justify-center border text-[10px]', checked ? 'border-signal bg-signal text-black' : 'border-mist/50')}>
                        {checked ? '✓' : null}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-paper" title={target.model}>{target.model}</span>
                        <span className="mt-0.5 block truncate text-xs text-mist" title={target.account.name}>
                          {target.account.name} · {target.account.provider}
                          {target.enabled ? '' : ' · 已关闭'}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
              {!isLoading && targets.length > 0 && !searchedTargets.length ? (
                <div className="py-10 text-center text-xs text-mist">没有匹配的模型或账号</div>
              ) : null}
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs text-signal">{selectedTargets.length} 个模型已选</span>
              <Button onClick={() => setSelectorOpen(false)}>完成选择</Button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </div>
  )
}
