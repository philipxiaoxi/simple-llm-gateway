import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleStop, Download, Gauge, Play, RotateCcw, Save, XCircle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api, type Account, type BenchmarkResult } from '../lib/api'
import { notifyBad, notifyOk } from '../lib/toast'
import { cn, errorMessage } from '../lib/utils'
import { Button, Field, Input, Select } from '../components/ui'

type Target = { account: Account; model: string }

export function BenchmarkPage() {
  const { data: accounts = [], isLoading } = useQuery({ queryKey: ['benchmark-accounts'], queryFn: api.keyAccounts })
  const [prompt, setPrompt] = useState('用一句话介绍你自己。')
  const [maxTokens, setMaxTokens] = useState('64')
  const [sourceFilter, setSourceFilter] = useState<'all' | 'upstream' | 'agent'>('all')
  const [providerFilter, setProviderFilter] = useState('all')
  const [accountFilter, setAccountFilter] = useState('all')
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
  const visibleTargets = targets.filter((target) => selected.includes(keyOf(target)))
  const visibleResults = visibleTargets.map((target) => results[keyOf(target)]).filter((result): result is BenchmarkResult => Boolean(result))
  const orderedResults = visibleResults.sort((left, right) => (left.first_token_ms ?? Infinity) - (right.first_token_ms ?? Infinity))

  function keyOf(target: Target) { return `${target.account.id}:${target.model}` }
  function toggle(target: Target) {
    const key = keyOf(target)
    setSelected((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  }
  function selectAll() { setSelected(selected.length === targets.length ? [] : targets.map(keyOf)) }
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

  return <div className="mx-auto max-w-7xl">
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div><div className="mb-2 font-mono text-xs tracking-[0.28em] text-signal">BENCHMARK LAB / LIVE</div><h1 className="text-3xl font-semibold tracking-tight">模型测速</h1><p className="mt-2 max-w-2xl text-sm text-mist">用同一条提示词测量所有账号模型的真实链路表现，关注首 token 延迟、完整响应时间与输出速度。</p></div>
      <div className="flex flex-wrap justify-end gap-2"><Button variant="ghost" onClick={() => void exportResults()}><Download size={16} />导出历史</Button><Button variant="ghost" onClick={() => void saveResults()} disabled={running || !visibleResults.length}><Save size={16} />保存本次</Button><Button variant="ghost" onClick={() => setResults({})}><RotateCcw size={16} />清空结果</Button>{running ? <Button variant="danger" onClick={stop}><CircleStop size={16} />停止</Button> : <Button onClick={() => void run()} disabled={isLoading || !visibleTargets.length}><Play size={16} />开始测速</Button>}</div>
    </div>
    <div className="grid gap-5 xl:grid-cols-[320px_1fr]">
      <section className="border border-line bg-panel/60 p-5">
        <div className="mb-5 flex items-center justify-between"><h2 className="font-semibold">测试配置</h2><Gauge size={19} className="text-signal" /></div>
        <div className="grid gap-4">
          <Field label="提示词"><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} className="min-h-24 w-full resize-y border border-line bg-black/20 p-3 text-sm text-paper outline-none focus:border-signal" /></Field>
          <Field label="最大输出 token"><Input type="number" min="1" max="512" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="账号来源"><Select value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value as typeof sourceFilter); setAccountFilter('all') }}><option value="all">全部来源</option><option value="upstream">上游账号</option><option value="agent">网关 Agent</option></Select></Field>
            <Field label="供应商筛选"><Select value={providerFilter} onChange={(event) => { setProviderFilter(event.target.value); setAccountFilter('all') }}><option value="all">全部供应商</option>{providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}</Select></Field>
          </div>
          <Field label="具体账号"><Select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)}><option value="all">全部账号</option>{filteredAccounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.source === 'agent' ? '网关 Agent' : '上游'}</option>)}</Select></Field>
          <div className="flex items-center justify-between border-t border-line pt-4 text-sm"><span className="text-mist">测试目标 <span className="font-mono text-xs">{selected.length}/{targets.length}</span></span><button type="button" className="text-signal hover:underline" onClick={selectAll}>{selected.length === targets.length ? '取消全选' : '全选模型'}</button></div>
          <div className="grid max-h-[42vh] grid-cols-1 gap-1.5 overflow-y-auto pr-1">{targets.map((target) => { const key = keyOf(target); const checked = selected.includes(key); return <button key={key} type="button" onClick={() => toggle(target)} className={cn('flex min-w-0 items-center gap-1.5 border px-2 py-1.5 text-left text-xs transition-colors', checked ? 'border-signal/50 bg-signal/10' : 'border-transparent hover:border-line hover:bg-white/5')}><span className={cn('flex h-3 w-3 shrink-0 items-center justify-center border text-[9px]', checked ? 'border-signal bg-signal text-black' : 'border-mist/50')}>{checked ? '✓' : null}</span><span className="min-w-0"><span className="block truncate text-paper" title={target.model}>{target.model}</span><span className="block truncate text-[10px] text-mist" title={target.account.name}>{target.account.name}</span></span></button> })}</div>
          {!isLoading && !targets.length ? <div className="text-xs text-mist">暂无已同步模型，请先在账号页刷新模型。</div> : null}
        </div>
      </section>
      <section className="min-w-0">
        <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">测速结果</h2><span className="font-mono text-xs text-mist">{orderedResults.length} / {visibleTargets.length} completed</span></div>
        <div className="overflow-x-auto border border-line bg-panel/40"><table className="w-full min-w-[760px] text-left text-sm"><thead className="border-b border-line bg-white/[0.03] text-xs text-mist"><tr><th className="px-4 py-3">账号 / 模型</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">首 token</th><th className="px-4 py-3">总耗时</th><th className="px-4 py-3">输出速度</th><th className="px-4 py-3">响应预览</th></tr></thead><tbody>{visibleTargets.map((target) => { const targetKey = keyOf(target); const result = results[targetKey]; const isTesting = testing.includes(targetKey); return <tr key={targetKey} className="border-b border-line/60 last:border-0"><td className="px-4 py-4"><div className="font-medium">{target.model}</div><div className="mt-1 text-xs text-mist">{target.account.name} · {target.account.provider}</div></td><td className="px-4 py-4">{isTesting ? <span className="text-signal">测试中</span> : !result ? <span className="text-mist">尚未测试</span> : result.timeout ? <span className="text-warning">超时</span> : result.ok ? <CheckCircle2 size={17} className="text-success" /> : <span title={result.error}><XCircle size={17} className="text-danger" /></span>}</td><td className="px-4 py-4 font-mono">{result?.first_token_ms ? `${result.first_token_ms} ms` : '—'}</td><td className="px-4 py-4 font-mono">{result?.total_ms ? `${result.total_ms} ms` : '—'}</td><td className="px-4 py-4 font-mono">{result?.output_tokens_per_second ? `${result.output_tokens_per_second} tok/s` : '—'}</td><td className="max-w-[260px] px-4 py-4 text-xs text-mist">{result?.preview || result?.error || (result?.ok ? '已返回，但未提取到文本' : '尚未测试')}</td></tr> })}</tbody></table></div>
        {orderedResults.length ? <div className="mt-5 grid gap-3 sm:grid-cols-3">{[['最快首 token', `${orderedResults[0].first_token_ms ?? 0} ms`], ['最快完整响应', `${Math.min(...orderedResults.map((item) => item.total_ms ?? Infinity))} ms`], ['最高输出速度', `${Math.max(...orderedResults.map((item) => item.output_tokens_per_second ?? 0))} tok/s`]].map(([label, value]) => <div key={label} className="border border-line bg-panel/40 p-4"><div className="text-xs text-mist">{label}</div><div className="mt-2 font-mono text-xl text-signal">{value}</div></div>)}</div> : null}
      </section>
    </div>
  </div>
}