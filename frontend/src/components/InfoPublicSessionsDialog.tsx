import { useQuery } from '@tanstack/react-query'
import { Fingerprint, Globe, History, Search, ShieldCheck, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api, type InfoPublicSession, type PublicGateScope } from '../lib/api'
import { relativeTime } from '../lib/info'
import { errorMessage, formatTime } from '../lib/utils'
import { Badge, Input } from './ui'

function deviceLabel(userAgent: string) {
  if (!userAgent) return '未知设备'
  const os = /Windows/.test(userAgent)
    ? 'Windows'
    : /Android/.test(userAgent)
      ? 'Android'
      : /iPhone|iPad|iPod/.test(userAgent)
        ? 'iOS'
        : /Mac OS X|Macintosh/.test(userAgent)
          ? 'macOS'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : ''
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /Firefox\//.test(userAgent)
      ? 'Firefox'
      : /Chrome\//.test(userAgent)
        ? 'Chrome'
        : /Version\/.*Safari/.test(userAgent)
          ? 'Safari'
          : /curl\//.test(userAgent)
            ? 'curl'
            : ''
  const parts = [browser, os].filter(Boolean)
  return parts.length ? parts.join(' · ') : userAgent.slice(0, 48)
}

function StatCard({ icon, label, value }: { icon: ReactNode; label: string; value: string | number }) {
  return (
    <div className="flex items-center gap-2.5 bg-panel px-4 py-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/5 text-signal">{icon}</span>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-paper">{value}</div>
        <div className="text-[11px] text-mist">{label}</div>
      </div>
    </div>
  )
}

function SessionRow({ row }: { row: InfoPublicSession }) {
  return (
    <div className="group flex items-center gap-3 rounded-xl border border-line/70 bg-panel-2/60 px-3.5 py-3 transition hover:border-signal/30">
      <span className="flex h-10 w-14 shrink-0 items-center justify-center rounded-lg border border-signal/20 bg-signal/10 font-mono text-[11px] tracking-[0.12em] text-signal">
        {row.code}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm tracking-wider text-paper">{row.code}</span>
          <Badge tone="mist">口令 v{row.gate_version}</Badge>
          {row.password_fingerprint ? (
            <Badge tone="info">指纹 {row.password_fingerprint}</Badge>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-mist">
          <span>{deviceLabel(row.user_agent)}</span>
          <span className="text-mist/40">·</span>
          <span className="inline-flex items-center gap-1">
            <Globe size={11} /> {row.ip || '未知 IP'}
          </span>
        </div>
      </div>
      <div className="shrink-0 text-right text-[11px]">
        <div className="text-mist">{relativeTime(row.created_at)}</div>
        <div className="mt-0.5 text-mist/60">{formatTime(row.created_at)}</div>
      </div>
    </div>
  )
}

export function InfoPublicSessionsDialog({
  open,
  scope,
  onClose,
}: {
  open: boolean
  scope: PublicGateScope
  onClose: () => void
}) {
  const sessions = useQuery({
    queryKey: ['public-gate-sessions', scope],
    queryFn: () => api.adminPublicSessions(scope, { limit: 100 }),
    enabled: open,
  })
  const [query, setQuery] = useState('')

  useEffect(() => {
    const body = document.body
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previous
    }
  }, [])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const rows = useMemo(() => {
    const keyword = query.trim().toUpperCase()
    const all = sessions.data?.sessions ?? []
    if (!keyword) return all
    return all.filter(
      (row) => row.code.includes(keyword) || row.ip.toUpperCase().includes(keyword),
    )
  }, [sessions.data, query])

  const uniqueIps = useMemo(() => new Set(rows.map((row) => row.ip).filter(Boolean)).size, [rows])

  if (!open) return null

  return createPortal(
    <div className="safe-area-modal fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm">
      <div className="my-[8vh] w-full max-w-2xl overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_40px_120px_-30px_rgba(0,0,0,0.9)]">
        <div className="relative border-b border-line px-5 py-4">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 h-24 opacity-60"
            style={{
              background:
                'radial-gradient(60% 100% at 0% 0%, rgba(200,245,66,0.16), transparent 70%),' +
                'radial-gradient(50% 100% at 100% 0%, rgba(110,200,255,0.14), transparent 70%)',
            }}
          />
          <div className="relative flex items-center gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-signal/30 bg-signal/10 text-signal">
              <History size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-semibold text-paper">访问记录</h2>
              <p className="mt-0.5 text-xs text-mist">
                按水印短码溯源：定位截图对应的访问时间、来源设备与口令版本
              </p>
            </div>
            <button
              type="button"
              aria-label="关闭"
              onClick={onClose}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-mist transition hover:bg-white/5 hover:text-paper"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-px border-b border-line bg-line/50">
          <StatCard icon={<Fingerprint size={15} />} label="记录条数" value={rows.length} />
          <StatCard icon={<Globe size={15} />} label="独立 IP" value={uniqueIps} />
          <StatCard
            icon={<ShieldCheck size={15} />}
            label="最近访问"
            value={rows[0] ? relativeTime(rows[0].created_at) : '—'}
          />
        </div>

        <div className="border-b border-line px-4 py-3">
          <div className="relative">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mist" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按短码或 IP 搜索"
              className="pl-9"
            />
          </div>
        </div>

        <div className="max-h-[46vh] space-y-2 overflow-y-auto p-3">
          {sessions.isLoading ? (
            <div className="py-10 text-center text-sm text-mist">加载中…</div>
          ) : sessions.isError ? (
            <div className="py-10 text-center text-sm text-danger">
              {errorMessage(sessions.error, '加载失败')}
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center text-mist">
              <Fingerprint size={22} />
              <span className="text-sm">{query ? '没有匹配的记录' : '暂无访问记录'}</span>
            </div>
          ) : (
            rows.map((row) => <SessionRow key={row.code} row={row} />)
          )}
        </div>

        <div className="border-t border-line px-5 py-3 text-[11px] leading-5 text-mist">
          记录保留最近 30 天；短码与解锁会话一一对应，口令版本变化后可区分改密前后的访问。
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default InfoPublicSessionsDialog
