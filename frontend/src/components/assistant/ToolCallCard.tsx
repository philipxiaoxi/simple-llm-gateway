import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Compass,
  ExternalLink,
  FileSearch,
  Loader2,
  PlugZap,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { toolLabel, toolLink, toolStatusLabel } from '../../lib/assistant'
import { cn, formatDuration } from '../../lib/utils'
import type { UiToolCall } from './AssistantProvider'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function apiIdFor(call: UiToolCall): string {
  const fromArgs = typeof call.arguments.api_id === 'string' ? call.arguments.api_id : ''
  return fromArgs || call.approval?.api_id || ''
}

function methodOf(call: UiToolCall): string {
  const apiId = apiIdFor(call)
  return apiId.split(' ')[0] || call.approval?.method || ''
}

function pathOf(call: UiToolCall): string {
  const previewPath = call.approval?.preview?.path
  if (previewPath) return previewPath
  const apiId = apiIdFor(call)
  return apiId.split(' ').slice(1).join(' ') || call.approval?.path || ''
}

function ToolIcon({ call, size = 14 }: { call: UiToolCall; size?: number }) {
  if (call.name === 'api_search') return <Search size={size} />
  if (call.name === 'api_describe') return <FileSearch size={size} />
  if (call.name === 'api_call') return <PlugZap size={size} />
  if (call.name === 'ui_navigate') return <Compass size={size} />
  return <PlugZap size={size} />
}

function methodTone(method: string) {
  if (method === 'GET' || method === 'HEAD') return 'border-ok/30 bg-ok/10 text-ok'
  if (method === 'POST') return 'border-warn/35 bg-warn/10 text-warn'
  if (method === 'DELETE') return 'border-danger/35 bg-danger/10 text-danger'
  return 'border-info/30 bg-info/10 text-info'
}

function apiLink(call: UiToolCall): string | null {
  const apiId = apiIdFor(call)
  const data = asRecord(call.result)
  const id = String(data?.id ?? data?.job_id ?? data?.site_id ?? '')
  if (!id) return toolLink(call.name, call.result)
  if (apiId.includes('/mcp/douyin')) return `/mcp-plaza/douyin/${id}`
  if (apiId.includes('/mcp/sites')) return `/mcp-plaza/sites/${id}`
  if (apiId.includes('/mcp/diagrams')) return `/mcp-plaza/diagrams/${id}`
  return toolLink(call.name, call.result)
}

function collectImages(result: unknown): string[] {
  const data = asRecord(result)
  if (!data) return []
  const lists = [data.media, data.results, data.items].filter(Array.isArray) as unknown[][]
  const urls: string[] = []
  for (const list of lists) {
    for (const item of list) {
      const record = asRecord(item)
      if (!record) continue
      const contentType = String(record.content_type ?? record.mime_type ?? '')
      const candidate = String(record.thumb_url ?? record.url ?? record.cover_url ?? record.image_url ?? '')
      if (!candidate) continue
      if (contentType && !contentType.startsWith('image/') && !record.thumb_url) continue
      if (!/^https?:\/\//.test(candidate) && !candidate.startsWith('/')) continue
      if (!urls.includes(candidate)) urls.push(candidate)
      if (urls.length >= 4) return urls
    }
  }
  const cover = String(data.cover_url ?? data.thumb_url ?? '')
  if (cover && !urls.includes(cover)) urls.unshift(cover)
  return urls.slice(0, 4)
}

function collectDownload(result: unknown): string | null {
  const data = asRecord(result)
  if (!data) return null
  const direct = String(data.download_url ?? data.absolute_download_url ?? '')
  if (direct) return direct
  const lists = [data.media, data.results, data.items].filter(Array.isArray) as unknown[][]
  for (const list of lists) {
    for (const item of list) {
      const record = asRecord(item)
      if (!record) continue
      const value = String(record.download_url ?? record.absolute_download_url ?? record.url ?? '')
      if (value) return value
    }
  }
  return null
}

const SENSITIVE_KEYS = new Set(['api_key', 'api_key_encrypted', 'password', 'password_hash', 'secret', 'client_secret', 'token', 'access_token', 'refresh_token', 'key', 'authorization'])

function redactForDisplay(value: unknown, depth = 0): unknown {
  if (depth > 8) return value
  if (Array.isArray(value)) return value.map((item) => redactForDisplay(item, depth + 1))
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      result[key] = SENSITIVE_KEYS.has(key.toLowerCase()) && item != null && item !== '' ? '***' : redactForDisplay(item, depth + 1)
    }
    return result
  }
  return value
}

function safeUrl(value: string | null): string | null {
  if (!value) return null
  if (value.startsWith('/') || /^https?:\/\//i.test(value)) return value
  return null
}

function jsonPreview(value: unknown, max = 3600) {
  if (value == null) return ''
  try {
    const raw = JSON.stringify(value, null, 2)
    return raw.length > max ? `${raw.slice(0, max)}\n…` : raw
  } catch {
    return String(value)
  }
}

function SearchResults({ result }: { result: unknown }) {
  const data = asRecord(result)
  const items = Array.isArray(data?.items) ? data.items : []
  if (!items.length) return null
  return (
    <details className="group border-t border-line/60">
      <summary className="flex cursor-pointer list-none items-center gap-1 px-3 py-2 text-[11px] text-mist transition-colors hover:text-paper [&::-webkit-details-marker]:hidden">
        <ChevronDown size={12} className="shrink-0 transition-transform group-open:rotate-180" />
        接口信息
        <span className="text-mist-soft">· {items.length} 个</span>
      </summary>
      <div className="border-t border-line/60 px-2 py-1.5">
        {items.slice(0, 4).map((raw, index) => {
          const item = asRecord(raw)
          if (!item) return null
          const method = String(item.method || '')
          return (
            <div key={`${item.id || index}`} className="flex min-w-0 items-start gap-2 rounded-md px-1 py-1.5">
              <span className={cn('mt-0.5 shrink-0 rounded border px-1 py-0.5 font-mono text-[9px]', methodTone(method))}>
                {method || 'API'}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-[10px] text-mist-soft">{String(item.path || '')}</div>
                <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-mist">{String(item.summary || item.description || '')}</div>
              </div>
              {item.read_only === false ? (
                <span className="mt-0.5 shrink-0 text-[9px] text-warn">需审批</span>
              ) : null}
            </div>
          )
        })}
        {items.length > 4 ? <div className="px-1 py-1 text-[10px] text-mist-soft">还有 {items.length - 4} 个结果</div> : null}
      </div>
    </details>
  )
}

export function ToolCallCard({
  call,
  onDecideApproval,
}: {
  call: UiToolCall
  onDecideApproval: (approvalId: string, decision: 'approve' | 'reject') => void
}) {
  const method = methodOf(call)
  const path = pathOf(call)
  const approval = call.approval
  const approvalPending = approval?.status === 'pending'
  const images = collectImages(call.result)
  const download = collectDownload(call.result)
  const link = apiLink(call)
  const status = asRecord(call.result)?.status
  const statusText = typeof status === 'string' ? toolStatusLabel(status) : ''
  const isSearch = call.name === 'api_search'
  const isDescribe = call.name === 'api_describe'
  const isNavigate = call.name === 'ui_navigate'
  const rawNavigatePath = isNavigate && call.ok ? String(asRecord(call.result)?.path || call.arguments.path || '') : ''
  const navigatePath = rawNavigatePath.startsWith('/') ? rawNavigatePath : ''
  const externalHref = safeUrl(download || link)

  return (
    <div
      className={cn(
        'mt-2 overflow-hidden rounded-xl border bg-panel/70 text-xs transition-colors',
        call.status === 'error'
          ? 'border-danger/35'
          : approvalPending
            ? 'border-warn/40 shadow-[0_0_0_1px_rgba(245,185,66,0.08)]'
            : 'border-line/80',
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        <span
          className={cn(
            'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border',
            call.status === 'error'
              ? 'border-danger/30 bg-danger/10 text-danger'
              : approvalPending
                ? 'border-warn/30 bg-warn/10 text-warn'
                : call.status === 'running'
                  ? 'border-signal/30 bg-signal/10 text-signal'
                  : 'border-ok/25 bg-ok/10 text-ok',
          )}
        >
          <ToolIcon call={call} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5">
            {method ? (
              <span className={cn('shrink-0 rounded border px-1 py-0.5 font-mono text-[9px]', methodTone(method))}>
                {method}
              </span>
            ) : null}
            <span className="truncate font-medium text-paper">{toolLabel(call.name)}</span>
            {statusText ? <span className="shrink-0 text-mist-soft">· {statusText}</span> : null}
          </div>
          {path ? <div className="mt-0.5 truncate font-mono text-[10px] text-mist-soft">{path}</div> : null}
          <div className="mt-0.5 flex min-w-0 items-center gap-2 overflow-hidden text-[11px] text-mist-soft">
            {call.status === 'running' ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-signal">
                <Loader2 size={11} className="animate-spin" /> 执行中
              </span>
            ) : call.status === 'waiting' ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-warn">
                <Clock3 size={11} /> 等待审批
              </span>
            ) : call.status === 'error' ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-danger">
                <AlertTriangle size={11} /> 失败
              </span>
            ) : (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-ok">
                <CheckCircle2 size={11} /> 完成
              </span>
            )}
            {call.latencyMs ? (
              <span className="shrink-0 whitespace-nowrap tabular-nums">{formatDuration(call.latencyMs)}</span>
            ) : null}
            {call.summary ? <span className="min-w-0 flex-1 truncate">{call.summary}</span> : null}
          </div>
        </div>
        {isNavigate && navigatePath ? (
          <Link
            to={navigatePath}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-line bg-panel-2 px-2 text-[11px] text-paper transition-colors hover:border-signal/40 hover:text-signal"
          >
            <Compass size={12} />
            打开
          </Link>
        ) : externalHref ? (
          <a
            href={externalHref}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-line bg-panel-2 px-2 text-[11px] text-paper transition-colors hover:border-signal/40 hover:text-signal"
          >
            <ExternalLink size={12} />
            {download ? '下载' : '打开'}
          </a>
        ) : null}
      </div>

      {approval ? (
        <div
          className={cn(
            'border-t px-3 py-2.5',
            approvalPending ? 'border-warn/25 bg-warn/5' : 'border-line/60 bg-panel-2/40',
          )}
        >
          <div className="flex items-start gap-2">
            <ShieldCheck size={14} className={cn('mt-0.5 shrink-0', approvalPending ? 'text-warn' : 'text-mist')} />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-medium text-paper">
                {approvalPending
                  ? '这个操作需要你确认'
                  : approval.status === 'executed'
                    ? '操作已执行'
                    : approval.status === 'rejected'
                      ? '操作已拒绝'
                      : approval.status === 'approved' || approval.status === 'pending'
                        ? '正在执行操作'
                        : '操作执行失败'}
              </div>
              {approval.reason ? (
                <div className="mt-1 text-[11px] leading-5 text-mist">{approval.reason}</div>
              ) : null}
              {approval.body != null ? (
                <pre className="mt-1.5 max-h-28 overflow-auto rounded-lg border border-line/60 bg-ink/40 p-2 font-mono text-[10px] leading-4 text-mist-soft">
                  {jsonPreview(approval.body, 1600)}
                </pre>
              ) : null}
              {approvalPending ? (
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-signal px-3 text-[11px] font-medium text-on-signal transition-all hover:brightness-110 active:scale-95"
                    onClick={() => onDecideApproval(approval.id, 'approve')}
                  >
                    <CheckCircle2 size={13} />
                    批准执行
                  </button>
                  <button
                    type="button"
                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-panel px-3 text-[11px] text-mist transition-colors hover:border-danger/40 hover:text-danger"
                    onClick={() => onDecideApproval(approval.id, 'reject')}
                  >
                    <X size={13} />
                    拒绝
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {call.error && !approvalPending ? (
        <div className="border-t border-danger/20 bg-danger/5 px-3 py-2 text-[11px] leading-5 text-danger">
          {call.error}
        </div>
      ) : null}

      {isSearch ? <SearchResults result={call.result} /> : null}

      {isDescribe ? (
        <div className="border-t border-line/60 px-3 py-2 text-[11px] text-mist">
          已读取 {Array.isArray(asRecord(call.result)?.items) ? (asRecord(call.result)?.items as unknown[]).length : 0} 个接口的参数定义。
        </div>
      ) : null}

      {images.length ? (
        <div className={cn('grid gap-1.5 border-t border-line/60 p-2', images.length > 1 ? 'grid-cols-2' : 'grid-cols-1')}>
          {images.map((url) => (
            <a key={url} href={url} target="_blank" rel="noreferrer noopener" className="group/image relative block overflow-hidden rounded-lg border border-line bg-ink/60">
              <img src={url} alt="" loading="lazy" className="aspect-video w-full object-cover transition-transform duration-300 group-hover/image:scale-[1.02]" />
            </a>
          ))}
        </div>
      ) : null}

      {call.arguments && Object.keys(call.arguments).length ? (
        <details className="group border-t border-line/60">
          <summary className="flex cursor-pointer list-none items-center gap-1 px-3 py-2 text-[11px] text-mist transition-colors hover:text-paper [&::-webkit-details-marker]:hidden">
            <ChevronDown size={12} className="transition-transform group-open:rotate-180" />
            参数与结果
          </summary>
          <div className="max-h-64 overflow-auto border-t border-line/60 bg-ink/50 px-3 py-2">
            <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-5 text-mist">
              {jsonPreview(redactForDisplay(call.arguments))}
            </pre>
            {call.result != null ? (
              <pre className="mt-2 whitespace-pre-wrap break-all border-t border-line/50 pt-2 font-mono text-[11px] leading-5 text-mist-soft">
                {jsonPreview(redactForDisplay(call.result))}
              </pre>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  )
}
