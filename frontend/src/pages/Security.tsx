import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, Settings as SettingsIcon, ShieldAlert, Trash2 } from 'lucide-react'
import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { FilterBar } from '../components/filter-bar'
import { Pagination } from '../components/Pagination'
import { ChartSkeleton } from '../components/charts/chart-card'
import { Badge, Button, Card, Field, Input, Select, Sheet, Switch } from '../components/ui'
import { api, type SecurityEvent, type SecurityLocationEntry, type SecuritySettings } from '../lib/api'
import { LOG_PAGE_SIZE, cn, formatTime } from '../lib/utils'

// 图表按需加载：echarts 体积不小，别让安全中心的列表请求一起买单
const SecurityCharts = lazy(() =>
  import('../components/security-charts').then((module) => ({ default: module.SecurityCharts })),
)

type TabKey = 'login' | 'suspicious' | 'beacon' | 'all'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'all', label: '全部事件' },
  { key: 'login', label: '登录日志' },
  { key: 'suspicious', label: '异常与调试' },
  { key: 'beacon', label: '前端埋点' },
]

const SEVERITY_TONE: Record<string, 'ok' | 'bad' | 'warn' | 'mist' | 'info'> = {
  info: 'info',
  warn: 'warn',
  critical: 'bad',
}

const SEVERITY_LABEL: Record<string, string> = {
  info: '提示',
  warn: '警告',
  critical: '严重',
}

const EVENT_LABEL: Record<string, string> = {
  login_success: '登录成功',
  login_failure: '登录失败',
  login_locked: '登录锁定',
  logout: '退出登录',
  admin_credential_change: '凭据变更',
  suspicious_request: '异常请求',
  auth_fail_burst: '认证突增',
  frontend_beacon: '前端埋点',
}

const TAG_LABEL: Record<string, string> = {
  non_browser_ua: '非浏览器 UA',
  missing_marker: '缺少前端标识',
  origin_mismatch: '来源不符',
  scanner_ua: '扫描器 UA',
  probe_path: '路径探测',
  automation: '自动化',
  auth_fail_burst: '认证突增',
  unfamiliar_location: '非常用地点',
  local_network: '内网地址',
  location_unknown: '地点未知',
}

/** 标签筛选的展示顺序：先风险高的，再环境类 */
const TAG_OPTIONS = [
  'scanner_ua',
  'non_browser_ua',
  'missing_marker',
  'probe_path',
  'origin_mismatch',
  'automation',
  'auth_fail_burst',
  'unfamiliar_location',
  'local_network',
  'location_unknown',
] as const

const OUTCOME_LABEL: Record<string, string> = {
  success: '成功',
  rejected: '已拒绝',
  failed: '失败',
  unknown: '未知',
}

const OUTCOME_TONE: Record<string, 'ok' | 'bad' | 'warn' | 'mist'> = {
  success: 'ok',
  rejected: 'warn',
  failed: 'bad',
  unknown: 'mist',
}

function eventLabel(value: string) {
  return EVENT_LABEL[value] ?? value
}

function tagLabel(value: string) {
  return TAG_LABEL[value] ?? value
}

function tagTone(tag: string): 'bad' | 'warn' | 'mist' {
  if (tag === 'scanner_ua' || tag === 'auth_fail_burst') return 'bad'
  if (tag === 'unfamiliar_location' || tag === 'automation') return 'warn'
  return 'mist'
}

function isIpOrCidr(value: string): boolean {
  const text = value.trim()
  if (!text) return false
  const parts = text.split('/')
  if (parts.length > 2) return false
  const host = parts[0]
  const isV4 =
    /^(\d{1,3})(\.\d{1,3}){3}$/.test(host) && host.split('.').every((part) => Number(part) <= 255)
  const isV6 = host.includes(':') && /^[0-9a-fA-F:]+$/.test(host)
  if (!isV4 && !isV6) return false
  if (parts.length === 2) {
    const prefix = Number(parts[1])
    const max = isV4 ? 32 : 128
    if (!Number.isInteger(prefix) || prefix < 0 || prefix > max) return false
  }
  return true
}

function TagRow({ tags }: { tags: string[] }) {
  if (!tags.length) return <span className="text-xs text-mist">—</span>
  return (
    <div className="flex flex-wrap gap-1">
      {tags.map((tag) => (
        <Badge key={tag} tone={tagTone(tag)}>
          {tagLabel(tag)}
        </Badge>
      ))}
    </div>
  )
}

function StatCard({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <Card className="flex min-w-0 flex-col gap-1">
      <div className="text-xs uppercase tracking-[0.16em] text-mist">{label}</div>
      <div className={cn('text-2xl font-semibold tabular-nums', tone)}>{value}</div>
    </Card>
  )
}

function EventRow({ item, onOpen }: { item: SecurityEvent; onOpen: (id: number) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(item.id)}
      className="block w-full cursor-pointer px-4 py-3 text-left transition hover:bg-white/[0.03]"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge tone={SEVERITY_TONE[item.severity] ?? 'mist'}>{SEVERITY_LABEL[item.severity] ?? item.severity}</Badge>
        <span className="text-sm font-medium text-paper">{eventLabel(item.event_type)}</span>
        {item.actor ? <span className="text-xs text-mist">· {item.actor}</span> : null}
        <time className="ml-auto font-mono text-xs text-mist" title={formatTime(item.created_at)}>
          {formatTime(item.created_at)}
        </time>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-mist">
        <span className="font-mono" title={item.ip_location || undefined}>
          {item.client_ip || '—'}
        </span>
        {item.ip_location ? <span>{item.ip_location}</span> : null}
        {item.client_name ? (
          <span>
            · {item.client_name}
            {item.client_version ? ` ${item.client_version}` : ''}
          </span>
        ) : null}
        {item.device_type ? <span>· {item.device_type}</span> : null}
        {item.method || item.path ? (
          <span className="font-mono">
            · {item.method} {item.path}
            {item.status_code ? ` (${item.status_code})` : ''}
          </span>
        ) : null}
      </div>
      {item.risk_tags.length ? (
        <div className="mt-1.5">
          <TagRow tags={item.risk_tags} />
        </div>
      ) : null}
    </button>
  )
}

function DetailSheet({ eventId, onClose }: { eventId: number; onClose: () => void }) {
  const { data, isPending } = useQuery({
    queryKey: ['security-event', eventId],
    queryFn: () => api.securityEvent(eventId),
  })
  const [reveal, setReveal] = useState(false)
  return (
    <Sheet title="事件详情" onClose={onClose}>
      {isPending || !data ? (
        <div className="py-8 text-center text-sm text-mist">加载中…</div>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-xs text-mist">事件</div>
              <div>{eventLabel(data.event_type)}</div>
            </div>
            <div>
              <div className="text-xs text-mist">级别</div>
              <Badge tone={SEVERITY_TONE[data.severity] ?? 'mist'}>{SEVERITY_LABEL[data.severity] ?? data.severity}</Badge>
            </div>
            <div>
              <div className="text-xs text-mist">来源</div>
              <div>{data.source}</div>
            </div>
            <div>
              <div className="text-xs text-mist">时间</div>
              <div className="font-mono text-xs">{formatTime(data.created_at)}</div>
            </div>
            <div>
              <div className="text-xs text-mist">账号</div>
              <div>{data.actor || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-mist">客户端标识</div>
              <div>
                {data.client_name || '—'}
                {data.client_version ? <span className="text-mist"> {data.client_version}</span> : null}
              </div>
            </div>
            <div>
              <div className="text-xs text-mist">设备</div>
              <div>{data.device_type || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-mist">响应结果</div>
              <div className="flex items-center gap-2">
                {data.outcome ? (
                  <Badge tone={OUTCOME_TONE[data.outcome] ?? 'mist'}>{OUTCOME_LABEL[data.outcome] ?? data.outcome}</Badge>
                ) : null}
                <span className="font-mono text-xs text-mist">
                  {data.status_code != null ? `HTTP ${data.status_code}` : '—'}
                </span>
              </div>
            </div>
            <div>
              <div className="text-xs text-mist">来源 IP</div>
              <div className="font-mono">{data.client_ip || '—'}</div>
            </div>
            <div>
              <div className="text-xs text-mist">归属地</div>
              <div>
                {data.ip_location || '—'}
                {data.location_rule ? <span className="text-mist"> · {data.location_rule}</span> : null}
              </div>
            </div>
          </div>
          {data.user_agent ? (
            <div>
              <div className="text-xs text-mist">User-Agent</div>
              <div className="break-all font-mono text-xs text-mist-strong">{data.user_agent}</div>
            </div>
          ) : null}
          {data.request_headers ? (
            <div>
              <div className="mb-1 text-xs text-mist">请求头（已脱敏）</div>
              <pre className="max-h-64 overflow-auto rounded-md border border-line bg-control px-3 py-2 font-mono text-xs text-mist-strong">
                {JSON.stringify(data.request_headers, null, 2)}
              </pre>
            </div>
          ) : null}
          {data.risk_tags.length ? (
            <div>
              <div className="mb-1 text-xs text-mist">风险标签</div>
              <TagRow tags={data.risk_tags} />
            </div>
          ) : null}
          {data.event_type === 'login_failure' || data.event_type === 'login_locked' ? (
            <div>
              <div className="mb-1 text-xs text-mist">尝试密码</div>
              {data.password_unavailable ? (
                <div className="text-xs text-mist">无法解密（主密钥可能已更换）</div>
              ) : reveal ? (
                <div className="break-all rounded-md border border-line bg-control px-3 py-2 font-mono text-xs">
                  {data.attempted_password ?? '—'}
                </div>
              ) : (
                <Button variant="line" onClick={() => setReveal(true)}>
                  显示本次尝试密码
                </Button>
              )}
            </div>
          ) : null}
          {data.detail ? (
            <div>
              <div className="mb-1 text-xs text-mist">明细</div>
              <pre className="max-h-64 overflow-auto rounded-md border border-line bg-control px-3 py-2 font-mono text-xs text-mist-strong">
                {JSON.stringify(data.detail, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      )}
    </Sheet>
  )
}

function SectionTitle({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-paper">{title}</h2>
        {hint ? <p className="mt-0.5 text-xs text-mist">{hint}</p> : null}
      </div>
      {action}
    </div>
  )
}

function LocationEntryRow({
  entry,
  onChange,
  onRemove,
}: {
  entry: SecurityLocationEntry
  onChange: (enabled: boolean) => void
  onRemove: () => void
}) {
  const normalized = [entry.country, entry.province, entry.city].filter(Boolean)
  const raw = entry.raw || normalized.join(' ')
  return (
    <div className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-paper" title={raw}>
          {raw}
        </div>
        {normalized.length ? (
          <div className="mt-0.5 truncate text-xs text-mist">
            {normalized.join(' / ')}
            {!entry.city && entry.province ? <span className="text-mist-soft">（省级，覆盖该省全部城市）</span> : null}
            {!entry.province && !entry.city ? <span className="text-mist-soft">（国家级）</span> : null}
          </div>
        ) : null}
      </div>
      <Switch checked={entry.enabled} onCheckedChange={onChange} />
      <button
        type="button"
        aria-label="移除该地点"
        className="inline-flex min-h-9 min-w-9 cursor-pointer items-center justify-center rounded-md text-mist transition hover:bg-danger/10 hover:text-danger"
        onClick={onRemove}
      >
        <Trash2 size={16} />
      </button>
    </div>
  )
}

function SettingsPanel({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['security-settings'], queryFn: api.securitySettings })
  const [enabled, setEnabled] = useState(true)
  const [locations, setLocations] = useState<SecurityLocationEntry[]>([])
  const [allowlist, setAllowlist] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const [ipDraft, setIpDraft] = useState('')
  const [ipError, setIpError] = useState('')
  const [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!data) return
    setEnabled(data.location_check_enabled)
    setLocations(data.trusted_locations)
    setAllowlist(data.ip_allowlist)
    setDirty(false)
  }, [data])

  const mutation = useMutation({
    mutationFn: (payload: SecuritySettings) => api.updateSecuritySettings(payload),
    onSuccess: (result) => {
      queryClient.setQueryData(['security-settings'], result)
      setSaved(true)
      setDirty(false)
      window.setTimeout(() => setSaved(false), 2400)
    },
  })

  function markDirty() {
    setDirty(true)
    setSaved(false)
  }

  function addLocation() {
    const value = draft.trim()
    if (!value) return
    setLocations((prev) => [...prev, { country: '', province: '', city: '', raw: value, enabled: true }])
    setDraft('')
    markDirty()
  }

  function addAllowlist() {
    const value = ipDraft.trim()
    if (!value) return
    if (!isIpOrCidr(value)) {
      setIpError('请输入合法的 IP 或 CIDR，例如 1.2.3.4 或 10.0.0.0/8')
      return
    }
    if (allowlist.includes(value)) {
      setIpError('该地址已在白名单中')
      return
    }
    setAllowlist((prev) => [...prev, value])
    setIpDraft('')
    setIpError('')
    markDirty()
  }

  function save() {
    mutation.mutate({
      location_check_enabled: enabled,
      trusted_locations: locations,
      ip_allowlist: allowlist,
    })
  }

  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <span className="text-xs text-mist">
        {mutation.isPending
          ? '保存中…'
          : mutation.isError
            ? <span className="text-danger">保存失败，请重试</span>
            : saved
              ? <span className="text-signal">已保存</span>
              : dirty
                ? '有未保存的修改'
                : '设置已是最新'}
      </span>
      <Button onClick={save} disabled={mutation.isPending || !dirty}>
        {mutation.isPending ? '保存中…' : '保存设置'}
      </Button>
    </div>
  )

  return (
    <Sheet title="安全设置" onClose={onClose} size="lg:max-w-3xl" mobileFull footer={footer}>
      <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
        <Card className="space-y-4">
          <SectionTitle
            title="常用地点"
            hint="来源归属地命中白名单即视为可信；未命中标记「非常用地点」。"
            action={<Switch checked={enabled} onCheckedChange={(next) => { setEnabled(next); markDirty() }} />}
          />

          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="中国 广东省 广州市"
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  addLocation()
                }
              }}
            />
            <Button variant="line" className="shrink-0 sm:w-auto" onClick={addLocation} disabled={!draft.trim()}>
              <Plus size={16} />
              添加
            </Button>
          </div>
          <p className="text-xs text-mist">
            支持三级：国家（中国）、省（中国 广东省，覆盖全省）、省市（中国 广东省 广州市）。
          </p>

          {locations.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line/80 px-4 py-8 text-center text-sm text-mist">
              未配置常用地点。未配置时所有公网来源都会标记为非常用地点。
            </div>
          ) : (
            <div className="divide-y divide-line/60 overflow-hidden rounded-lg border border-line/70">
              {locations.map((entry, index) => (
                <LocationEntryRow
                  key={`${entry.raw}-${index}`}
                  entry={entry}
                  onChange={(next) => {
                    setLocations((prev) => prev.map((it, i) => (i === index ? { ...it, enabled: next } : it)))
                    markDirty()
                  }}
                  onRemove={() => {
                    setLocations((prev) => prev.filter((_, i) => i !== index))
                    markDirty()
                  }}
                />
              ))}
            </div>
          )}
        </Card>

        <Card className="space-y-4">
          <SectionTitle title="IP / CIDR 白名单" hint="命中的来源直接视为可信，跳过多地点判定。" />

          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={ipDraft}
              onChange={(event) => {
                setIpDraft(event.target.value)
                if (ipError) setIpError('')
              }}
              placeholder="1.2.3.4 或 10.0.0.0/8"
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  addAllowlist()
                }
              }}
            />
            <Button variant="line" className="shrink-0 sm:w-auto" onClick={addAllowlist} disabled={!ipDraft.trim()}>
              <Plus size={16} />
              添加
            </Button>
          </div>
          {ipError ? <p className="text-xs text-danger">{ipError}</p> : null}

          {allowlist.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line/80 px-4 py-8 text-center text-sm text-mist">
              暂无白名单。适合固定出口 IP 或内网网段。
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {allowlist.map((item, index) => (
                <span
                  key={`${item}-${index}`}
                  className="inline-flex items-center gap-2 rounded-full border border-line bg-control py-1 pl-3 pr-1.5 font-mono text-xs text-paper"
                >
                  {item}
                  <button
                    type="button"
                    aria-label={`移除 ${item}`}
                    className="inline-flex min-h-7 min-w-7 cursor-pointer items-center justify-center rounded-full text-mist transition hover:bg-danger/10 hover:text-danger"
                    onClick={() => {
                      setAllowlist((prev) => prev.filter((_, i) => i !== index))
                      markDirty()
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </Card>
      </div>
    </Sheet>
  )
}

export function SecurityPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<TabKey>('all')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [days, setDays] = useState(7)
  const [page, setPage] = useState(1)
  const [severity, setSeverity] = useState('')
  const [source, setSource] = useState('')
  const [ip, setIp] = useState('')
  const [search, setSearch] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [selected, setSelected] = useState<number | null>(null)

  const overview = useQuery({ queryKey: ['security-overview', days], queryFn: () => api.securityOverview(days) })
  const events = useQuery({
    queryKey: ['security-events', tab, page, severity, source, ip, search, tags],
    queryFn: () =>
      api.securityEvents({
        group: tab === 'all' ? undefined : tab,
        severity: severity || undefined,
        source: source || undefined,
        ip: ip || undefined,
        q: search || undefined,
        tags: tags.length ? tags : undefined,
        page,
        page_size: LOG_PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  })

  useEffect(() => {
    setPage(1)
  }, [tab, severity, source, ip, search, tags])

  const items = events.data?.items ?? []
  const total = events.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / LOG_PAGE_SIZE))
  const activeFilterCount = useMemo(
    () => [severity, source, ip, search].filter(Boolean).length + tags.length,
    [severity, source, ip, search, tags],
  )

  const severityCounts = overview.data?.severity_counts ?? {}

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ShieldAlert size={20} className="text-signal" />
            <h1 className="text-2xl font-semibold">安全中心</h1>
          </div>
          <p className="mt-1 text-sm text-mist">登录审计、异常请求标记与前端埋点，集中查看与筛选。</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={days} onChange={(event) => setDays(Number(event.target.value))} className="w-28">
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
            <option value={90}>近 90 天</option>
          </Select>
          <Button
            variant={settingsOpen ? 'primary' : 'line'}
            aria-pressed={settingsOpen}
            onClick={() => setSettingsOpen((prev) => !prev)}
          >
            <SettingsIcon size={14} /> 设置
          </Button>
          <Button
            variant="line"
            onClick={() => {
              void queryClient.invalidateQueries({ queryKey: ['security-overview'] })
              void queryClient.invalidateQueries({ queryKey: ['security-events'] })
              void queryClient.invalidateQueries({ queryKey: ['security-settings'] })
            }}
          >
            <RefreshCw size={14} className={cn(events.isFetching && 'animate-spin')} /> 刷新
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="登录成功" value={overview.data?.login_success ?? 0} tone="text-signal" />
        <StatCard label="登录失败" value={overview.data?.login_failure ?? 0} tone="text-danger" />
        <StatCard label="异常请求" value={overview.data?.suspicious ?? 0} tone="text-warn" />
        <StatCard label="来源 IP 数" value={overview.data?.distinct_ips ?? 0} />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-mist">
        <span>
          严重 <span className="tabular-nums text-danger">{severityCounts.critical ?? 0}</span>
        </span>
        <span>
          警告 <span className="tabular-nums text-warn">{severityCounts.warn ?? 0}</span>
        </span>
        <span>
          提示 <span className="tabular-nums text-info">{severityCounts.info ?? 0}</span>
        </span>
        <span className="ml-auto">
          数据截至 {overview.data ? formatTime(overview.data.until) : '—'}
        </span>
      </div>

      <Suspense
        fallback={
          <div className="grid gap-4 xl:grid-cols-2">
            <ChartSkeleton />
            <ChartSkeleton />
          </div>
        }
      >
        {overview.data ? <SecurityCharts data={overview.data} /> : null}
      </Suspense>

      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setTab(item.key)}
            className={cn(
              'shrink-0 cursor-pointer whitespace-nowrap rounded-full border px-3 py-1.5 text-sm transition',
              tab === item.key
                ? 'border-signal/40 bg-signal/10 text-signal'
                : 'border-line bg-white/[0.02] text-mist hover:text-paper',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      <FilterBar
        activeCount={activeFilterCount}
        onReset={() => {
          setSeverity('')
          setSource('')
          setIp('')
          setSearch('')
          setTags([])
        }}
        desktopClassName="grid gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_9rem_9rem_11rem] lg:items-end"
      >
        <Field label="搜索">
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="路径 / 账号 / UA / 归属地" />
        </Field>
        <Field label="级别">
          <Select value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="">全部级别</option>
            <option value="info">提示</option>
            <option value="warn">警告</option>
            <option value="critical">严重</option>
          </Select>
        </Field>
        <Field label="来源">
          <Select value={source} onChange={(event) => setSource(event.target.value)}>
            <option value="">全部来源</option>
            <option value="web">web</option>
            <option value="api">api</option>
            <option value="direct">direct</option>
          </Select>
        </Field>
        <Field label="来源 IP">
          <Input value={ip} onChange={(event) => setIp(event.target.value)} placeholder="精确匹配" />
        </Field>
        <div className="min-w-0 lg:col-span-full">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">标签（可多选）</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {TAG_OPTIONS.map((tag) => {
              const active = tags.includes(tag)
              return (
                <button
                  key={tag}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    setTags((prev) => (active ? prev.filter((item) => item !== tag) : [...prev, tag]))
                  }
                  className={cn(
                    'cursor-pointer rounded-full border px-2.5 py-1 text-xs transition',
                    active
                      ? 'border-signal/40 bg-signal/10 text-signal'
                      : 'border-line bg-white/[0.02] text-mist hover:text-paper',
                  )}
                >
                  {tagLabel(tag)}
                </button>
              )
            })}
          </div>
        </div>
      </FilterBar>

      <div className="divide-y divide-line/60 overflow-hidden rounded-xl border border-line/70 bg-panel/40">
        {items.map((item) => (
          <EventRow key={item.id} item={item} onOpen={setSelected} />
        ))}
        {events.isPending && items.length === 0 ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="h-14 animate-pulse rounded-lg bg-white/[0.04]" />
            ))}
          </div>
        ) : null}
        {!events.isPending && items.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-mist">暂无安全事件。</div>
        ) : null}
      </div>

      <Pagination
        page={page}
        pageCount={pageCount}
        total={total}
        unit="条事件"
        onPage={(next) => setPage(Math.min(Math.max(next, 1), pageCount))}
      />

      {settingsOpen ? <SettingsPanel onClose={() => setSettingsOpen(false)} /> : null}
      {selected != null ? <DetailSheet eventId={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  )
}
