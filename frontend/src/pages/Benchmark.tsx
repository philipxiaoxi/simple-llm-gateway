import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleStop, Download, Gauge, Info, PanelLeftClose, PanelLeftOpen, Play, RotateCcw, Save, Search, XCircle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api, type Account, type BenchmarkResult } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { Button, Dialog, Field, Input, Select } from '../components/ui'

type Target = { account: Account; model: string }

export function BenchmarkPage() {
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
      .flatMap((account) => account.models.map((model) => ({ account, model }))),
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

  function keyOf(target: Target) { return `${target.account.id}:${target.model}` }
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

  return <div className="flex h-full min-h-0 min-w-0 w-full flex-col">
    <div className="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-4">
      <div><div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK LAB / LIVE</div><h1 className="text-3xl font-semibold tracking-tight">模型测速</h1><p className="mt-2 max-w-2xl text-sm text-mist">用同一条提示词测量真实链路。</p></div>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="ghost" onClick={() => void exportResults()}><Download size={16} />导出历史</Button><Button variant="ghost" onClick={() => void saveResults()} disabled={running || !visibleResults.length}><Save size={16} />保存本次</Button><Button variant="ghost" onClick={() => setResults({})}><RotateCcw size={16} />清空结果</Button>{running ? <Button variant="danger" onClick={stop}><CircleStop size={16} />停止</Button> : <Button onClick={() => void run()} disabled={isLoading || !visibleTargets.length}><Play size={16} />开始测速</Button>}</div>
    </div>
    <div className={cn('grid min-h-0 flex-1 gap-6', configCollapsed ? 'xl:grid-cols-[56px_minmax(0,1fr)]' : 'xl:grid-cols-[minmax(390px,0.82fr)_minmax(0,1.8fr)]')}>
      <section className={cn('flex min-h-0 flex-col overflow-hidden border border-line bg-panel/60', configCollapsed ? 'items-center p-2' : 'p-5')}>
        <div className={cn('flex items-center', configCollapsed ? 'flex-col gap-3' : 'mb-5 justify-between')}>
          {!configCollapsed ? <div className="flex items-center gap-2"><h2 className="font-semibold">测试配置</h2><Gauge size={19} className="text-signal" /></div> : <Gauge size={19} className="text-signal" />}
          <button type="button" className="text-mist hover:text-paper" onClick={() => setConfigCollapsed((current) => !current)} aria-label={configCollapsed ? '展开测试配置' : '折叠测试配置'} title={configCollapsed ? '展开测试配置' : '折叠测试配置'}>
            {configCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        </div>
        {!configCollapsed ? <div className="grid min-h-0 flex-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(170px,0.75fr)]">
          <div className="grid min-h-0 content-start gap-4">
            <Field label="提示词"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-h-24 w-full resize-y border border-line bg-black/20 p-3 text-sm text-paper outline-none focus:border-signal" /></Field>
            <Field label="最大输出 token"><Input type="number" min="1" max="512" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="账号来源"><Select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value as typeof sourceFilter); setAccountFilter('all') }}><option value="all">全部来源</option><option value="upstream">上游账号</option><option value="agent">网关代理</option></Select></Field>
              <Field label="供应商筛选"><Select value={providerFilter} onChange={(event) => { setProviderFilter(event.target.value); setAccountFilter('all') }}><option value="all">全部供应商</option>{providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}</Select></Field>
            </div>
            <Field label="具体账号"><Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}><option value="all">全部账号</option>{filteredAccounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.source === 'agent' ? '网关代理' : '上游'}</option>)}</Select></Field>
            <div className="border-t border-line pt-4">
              <div className="flex items-center justify-between text-sm"><span className="font-semibold">选择测试模型</span><span className="font-mono text-xs text-signal">{selectedTargets.length} 已选</span></div>
              <div className="mt-3 flex items-center justify-between gap-3 text-[11px] text-mist"><span>共 {targets.length} 个可用模型</span><Button variant="line" className="min-h-9 px-3 text-xs" onClick={() => setSelectorOpen(true)}><Search size={14} />选择模型</Button></div>
            </div>
            {!isLoading && !targets.length ? <div className="text-xs text-mist">暂无已同步模型，请先在账号页刷新模型。</div> : null}
          </div>
          <div className="flex min-h-0 flex-col border-l border-line pl-4">
            <div className="mb-3 flex items-center justify-between gap-2 text-[11px] text-mist"><span>已选测试目标</span><button type="button" className="shrink-0 hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={clearSelected} disabled={!selected.length}>清空已选</button></div>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <div className="grid gap-1.5">{selectedTargets.map((target) => <div key={keyOf(target)} className="flex min-w-0 items-center gap-2 border border-signal/30 bg-signal/10 px-3 py-2 text-xs"><span className="min-w-0 flex-1"><span className="block truncate text-paper" title={target.model}>{target.model}</span><span className="mt-0.5 block truncate text-[10px] text-mist" title={target.account.name}>{target.account.name} · {target.account.provider}</span></span><button type="button" className="shrink-0 text-mist hover:text-danger" onClick={() => toggle(target)} aria-label={`移除 ${target.model}`}>×</button></div>)}</div>
              {!selectedTargets.length ? <div className="py-8 text-center text-xs text-mist">还没有选择模型<br />点击“选择模型”开始配置</div> : null}
            </div>
          </div>
        </div> : null}
      </section>
      <section className="flex min-h-0 min-w-0 flex-col">
        <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">测速结果</h2><span className="font-mono text-xs text-mist">{orderedResults.length} / {visibleTargets.length} completed</span></div>
        <div className="min-h-0 flex-1 overflow-auto border border-line bg-panel/40"><table className="w-full min-w-[760px] text-left text-sm"><thead className="sticky top-0 z-10 border-b border-line bg-panel text-xs text-mist"><tr><th className="px-4 py-3">账号 / 模型</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">首 token</th><th className="px-4 py-3">总耗时</th><th className="px-4 py-3"><span className="inline-flex items-center gap-1">输出速度<span className="group relative inline-flex" tabIndex={0}><Info size={13} className="cursor-help text-mist group-hover:text-paper group-focus-visible:text-paper" aria-label="输出速度计算公式" /><span className="pointer-events-none absolute left-0 top-full z-20 mt-2 hidden w-72 border border-line bg-panel-2 p-3 text-left text-[11px] leading-5 font-normal text-mist shadow-[0_12px_32px_rgba(0,0,0,0.45)] group-hover:block group-focus-visible:block"><span className="block font-mono text-paper">tok/s = completion_tokens ÷ ((总耗时 − 首 token) / 1000)</span><span className="mt-1.5 block">分子取上游 usage 的 completion_tokens（含思考，不再另加 reasoning_tokens）。分母为首 token 之后的生成窗口。无 usage 时不显示速度。</span></span></span></span></th><th className="px-4 py-3">响应预览</th></tr></thead><tbody>{visibleTargets.map((target) => { const targetKey = keyOf(target); const result = results[targetKey]; const isTesting = testing.includes(targetKey); return <tr key={targetKey} className="border-b border-line/60 last:border-0"><td className="px-4 py-4"><div className="font-medium">{target.model}</div><div className="mt-1 text-xs text-mist">{target.account.name} · {target.account.provider}</div></td><td className="px-4 py-4">{isTesting ? <span className="text-signal">测试中</span> : !result ? <span className="text-mist">尚未测试</span> : result.timeout ? <span className="text-warning">超时</span> : result.ok ? <CheckCircle2 size={17} className="text-success" /> : <span title={result.error}><XCircle size={17} className="text-danger" /></span>}</td><td className="px-4 py-4 font-mono">{result?.first_token_ms ? `${result.first_token_ms} ms` : '—'}</td><td className="px-4 py-4 font-mono">{result?.total_ms ? `${result.total_ms} ms` : '—'}</td><td className="px-4 py-4 font-mono">{result?.output_tokens_per_second ? `${result.output_tokens_per_second} tok/s` : '—'}</td><td className="max-w-[260px] px-4 py-4 text-xs text-mist">{result?.preview || result?.error || (result?.ok ? '已返回，但未提取到文本' : '尚未测试')}</td></tr> })}</tbody></table></div>
        {orderedResults.length ? <div className="mt-5 grid gap-3 sm:grid-cols-3">{[['最快首 token', `${orderedResults[0].first_token_ms ?? 0} ms`], ['最快完整响应', `${Math.min(...orderedResults.map((item) => item.total_ms ?? Infinity))} ms`], ['最高输出速度', `${Math.max(...orderedResults.map((item) => item.output_tokens_per_second ?? 0))} tok/s`]].map(([label, value]) => <div key={label} className="border border-line bg-panel/40 p-4"><div className="text-xs text-mist">{label}</div><div className="mt-2 font-mono text-xl text-signal">{value}</div></div>)}</div> : null}
      </section>
    </div>
    {selectorOpen ? <Dialog title="选择测试模型" className="max-w-5xl" onClose={() => setSelectorOpen(false)}>
      <div className="flex max-h-[min(680px,calc(100vh-10rem))] flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="账号来源"><Select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value as typeof sourceFilter); setAccountFilter('all') }}><option value="all">全部来源</option><option value="upstream">上游账号</option><option value="agent">网关代理</option></Select></Field>
          <Field label="供应商筛选"><Select value={providerFilter} onChange={(event) => { setProviderFilter(event.target.value); setAccountFilter('all') }}><option value="all">全部供应商</option>{providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}</Select></Field>
        </div>
        <Field label="具体账号"><Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}><option value="all">全部账号</option>{filteredAccounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.source === 'agent' ? '网关代理' : '上游'}</option>)}</Select></Field>
        <div className="relative"><Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" /><Input autoFocus value={targetSearch} onChange={(event) => setTargetSearch(event.target.value)} placeholder="搜索模型、账号或供应商" className="pl-9" /></div>
        <div className="flex items-center justify-between text-xs text-mist"><span>搜索结果 {searchedTargets.length} 个</span><div className="flex gap-3"><button type="button" className="text-signal hover:underline" onClick={selectAll}>{searchedTargets.length > 0 && searchedTargets.every((target) => selected.includes(keyOf(target))) ? '取消全选' : '全选搜索结果'}</button><button type="button" className="hover:text-paper disabled:cursor-not-allowed disabled:opacity-40" onClick={clearSelected} disabled={!selected.length}>清空已选</button></div></div>
        <div className="min-h-0 flex-1 overflow-y-auto border-y border-line py-2"><div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{searchedTargets.map((target) => { const key = keyOf(target); const checked = selected.includes(key); return <button key={key} type="button" onClick={() => toggle(target)} className={cn('flex w-full min-w-0 items-center gap-3 border px-3 py-2.5 text-left text-sm transition-colors', checked ? 'border-signal/50 bg-signal/10' : 'border-transparent hover:border-line hover:bg-white/5')}><span className={cn('flex h-4 w-4 shrink-0 items-center justify-center border text-[10px]', checked ? 'border-signal bg-signal text-black' : 'border-mist/50')}>{checked ? '✓' : null}</span><span className="min-w-0"><span className="block truncate text-paper" title={target.model}>{target.model}</span><span className="mt-0.5 block truncate text-xs text-mist" title={target.account.name}>{target.account.name} · {target.account.provider}</span></span></button> })}</div>{!isLoading && targets.length > 0 && !searchedTargets.length ? <div className="py-10 text-center text-xs text-mist">没有匹配的模型或账号</div> : null}</div>
        <div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-signal">{selectedTargets.length} 个模型已选</span><Button onClick={() => setSelectorOpen(false)}>完成选择</Button></div>
      </div>
    </Dialog> : null}
  </div>
}
