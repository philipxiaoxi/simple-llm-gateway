/**
 * 资讯收集（/info）的前端共享工具。
 *
 * 这里只放「纯函数 + 常量」：纯文字封面的确定性推导（配色 / 比例）、
 * 时间与数量的展示格式化、以及列表缓存的局部更新。
 * 契约见 `.monkeycode/specs/2026-10-04-info-collection/design.md`。
 */

import type { QueryClient } from '@tanstack/react-query'
import type { InfoItem, InfoItemDetail, InfoItemList } from './api'

/* ------------------------------------------------------------------ *
 * 纯文字封面（由 cover_seed 唯一决定，同一 seed 任何时候都得到同一张）
 * ------------------------------------------------------------------ */

export type InfoCoverPalette = {
  name: string
  /** 渐变起点 */
  from: string
  /** 渐变终点 */
  to: string
  /** 强调色 */
  accent: string
}

/** 6 套配色，顺序即 PALETTES[seed % 6] 的下标 */
export const INFO_COVER_PALETTES: InfoCoverPalette[] = [
  { name: 'signal', from: '#1b2410', to: '#0f1408', accent: '#c8f542' },
  { name: 'info', from: '#0f1d26', to: '#0a1218', accent: '#6ec8ff' },
  { name: 'warn', from: '#241c0f', to: '#16110a', accent: '#f5b942' },
  { name: 'danger', from: '#241016', to: '#160a0d', accent: '#f25f6d' },
  { name: 'ok', from: '#0f2418', to: '#0a1610', accent: '#4ade80' },
  { name: 'neutral', from: '#171b23', to: '#0e1116', accent: '#e8edf5' },
]

export type InfoCoverAspect = { label: string; width: number; height: number }

/** 3 种比例，顺序即 ASPECTS[(seed >> 6) % 3] 的下标 */
export const INFO_COVER_ASPECTS: InfoCoverAspect[] = [
  { label: '3:4', width: 3, height: 4 },
  { label: '1:1', width: 1, height: 1 },
  { label: '4:5', width: 4, height: 5 },
]

/** 契约：cover.width/height 缺失（探测失败）时的兜底比例 */
export const INFO_COVER_FALLBACK_ASPECT: InfoCoverAspect = { label: '4:5', width: 4, height: 5 }

function floorDiv(value: number, divisor: number) {
  const safe = Number.isFinite(value) ? value : 0
  // 与 Python 的 >> 一致：负数向下取整
  return Math.floor(Math.trunc(safe) / divisor)
}

function positiveMod(value: number, modulo: number) {
  const safe = Number.isFinite(value) ? Math.trunc(value) : 0
  return ((safe % modulo) + modulo) % modulo
}

/** PALETTES[seed % 6] */
export function infoCoverPalette(seed: number): InfoCoverPalette {
  return INFO_COVER_PALETTES[positiveMod(seed, INFO_COVER_PALETTES.length)]
}

/** ASPECTS[(seed >> 6) % 3] */
export function infoCoverAspect(seed: number): InfoCoverAspect {
  return INFO_COVER_ASPECTS[positiveMod(floorDiv(seed, 64), INFO_COVER_ASPECTS.length)]
}

/** 宽高比（width / height） */
export function infoCoverRatio(seed: number) {
  const aspect = infoCoverAspect(seed)
  return aspect.width / aspect.height
}

/** 把 6 位十六进制色转成带透明度的 rgba() */
export function withAlpha(hex: string, alpha: number) {
  const value = hex.replace('#', '')
  const red = Number.parseInt(value.slice(0, 2), 16)
  const green = Number.parseInt(value.slice(2, 4), 16)
  const blue = Number.parseInt(value.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

/**
 * 封面底：linear-gradient(160deg) 打底 + 右上角强调色光晕 + 3% 不透明度的 24px 网格纹理。
 * 返回可直接给 backgroundImage 的字符串（列表前面的层级在上）。
 */
export function infoCoverBackground(seed: number) {
  const palette = infoCoverPalette(seed)
  return [
    'repeating-linear-gradient(0deg, rgba(255, 255, 255, 0.03) 0 1px, transparent 1px 24px)',
    'repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.03) 0 1px, transparent 1px 24px)',
    `radial-gradient(120% 80% at 100% 0%, ${withAlpha(palette.accent, 0.18)}, transparent 60%)`,
    `linear-gradient(160deg, ${palette.from}, ${palette.to})`,
  ].join(', ')
}

const URL_PATTERN = /https?:\/\/\S+/gi
const MEANINGFUL_PATTERN = /[\p{L}\p{N}]/u

/**
 * 封面摘要：剥离 URL 与纯 emoji/符号行后取前 N 字。
 * 后端 excerpt 已做过一轮处理，这里再兜一层保证封面不会只有一串链接。
 */
export function coverExcerpt(text: string | null | undefined, limit = 80) {
  const raw = (text || '').replace(URL_PATTERN, ' ')
  const lines = raw
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
  const meaningful = lines.filter((line) => MEANINGFUL_PATTERN.test(line))
  const merged = (meaningful.length ? meaningful : lines).join(' ').replace(/\s+/g, ' ').trim()
  return merged.slice(0, limit)
}

/**
 * 封面上的标签：只取正文里真实存在的 #话题（去重、最多 3 个）。
 * 没有话题就返回空数组——不硬凑词，避免出现"其实是正文词"的假标签。
 */
export function coverTags(text: string | null | undefined) {
  const source = text || ''
  const hashtags = Array.from(source.matchAll(/#([\p{L}\p{N}_]{2,16})/gu)).map((match) => match[1])
  return Array.from(new Set(hashtags)).slice(0, 3)
}

/* ------------------------------------------------------------------ *
 * 卡片尺寸预估
 * ------------------------------------------------------------------ */

type RatioSource = { width?: number | null; height?: number | null }

function ratioOfSize(size: RatioSource | null | undefined) {
  const width = size?.width ?? 0
  const height = size?.height ?? 0
  if (width > 0 && height > 0) return width / height
  return 0
}

/**
 * 卡片占位宽高比：
 * - 纯文字（cover 为 null）→ cover_seed 决定的固定比例
 * - 有媒体 → 用真实宽高；探测失败的极少数回退 4:5
 */
export function infoItemRatio(item: Pick<InfoItem, 'cover' | 'cover_seed'>) {
  if (!item.cover) return infoCoverRatio(item.cover_seed)
  const ratio = ratioOfSize(item.cover)
  if (ratio) return ratio
  return INFO_COVER_FALLBACK_ASPECT.width / INFO_COVER_FALLBACK_ASPECT.height
}

/** 详情页单个媒体项的占位宽高比 */
export function infoMediaRatio(media: RatioSource | null | undefined, item: Pick<InfoItem, 'cover' | 'cover_seed'>) {
  return ratioOfSize(media) || infoItemRatio(item)
}

/* ------------------------------------------------------------------ *
 * 展示格式化
 * ------------------------------------------------------------------ */

const TIMEZONE_SUFFIX = /[zZ]|[+-]\d{2}:\d{2}$/

export function parseInfoTime(value: string | null | undefined): number | null {
  if (!value) return null
  const text = TIMEZONE_SUFFIX.test(value) ? value : `${value}Z`
  const parsed = Date.parse(text)
  return Number.isNaN(parsed) ? null : parsed
}

/** 相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 3 个月前 */
export function relativeTime(value: string | null | undefined, now = Date.now()) {
  const parsed = parseInfoTime(value)
  if (parsed == null) return ''
  const diff = now - parsed
  if (diff < 60_000) return '刚刚'
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months} 个月前`
  return `${Math.floor(months / 12)} 年前`
}

/** 千分位，避免 toLocaleString 在不同环境下的差异 */
export function formatCount(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return ''
  return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** 视频时长角标：02:14 / 1:02:03 */
export function formatDuration(value: number | null | undefined) {
  if (value == null || value <= 0) return ''
  const total = Math.round(value / 1000)
  const seconds = total % 60
  const minutes = Math.floor(total / 60) % 60
  const hours = Math.floor(total / 3600)
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes)
  return hours > 0
    ? `${hours}:${mm}:${String(seconds).padStart(2, '0')}`
    : `${mm}:${String(seconds).padStart(2, '0')}`
}

/** 采集间隔：1800 → 30 分钟 */
export function formatInterval(seconds: number | null | undefined) {
  if (seconds === 0) return '手动'
  if (!seconds || seconds < 0) return '—'
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`
  return `${seconds} 秒`
}

export const INFO_KIND_LABELS: Record<string, string> = {
  text: '纯文字',
  image: '图文',
  video: '视频',
  mixed: '图文视频',
}

export function infoKindLabel(kind: string | null | undefined) {
  if (!kind) return '内容'
  return INFO_KIND_LABELS[kind] || kind
}

/* ------------------------------------------------------------------ *
 * 列表缓存的局部更新（收藏/隐藏后不必重拉整条无限流）
 * ------------------------------------------------------------------ */

export function patchInfoItemCaches(client: QueryClient, id: string, patch: Partial<InfoItem>) {
  client.setQueriesData<InfoItemList>({ queryKey: ['info-items'] }, (data) => {
    if (!data?.items) return data
    return { ...data, items: data.items.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)) }
  })
  const detail = client.getQueryData<InfoItemDetail>(['info-item', id])
  if (detail) client.setQueryData<InfoItemDetail>(['info-item', id], { ...detail, ...patch })
}

export function removeInfoItemFromCaches(client: QueryClient, id: string) {
  client.setQueriesData<InfoItemList>({ queryKey: ['info-items'] }, (data) => {
    if (!data?.items) return data
    return { ...data, items: data.items.filter((entry) => entry.id !== id) }
  })
}

export function mergeInfoItemDetail(client: QueryClient, item: InfoItemDetail) {
  client.setQueryData<InfoItemDetail>(['info-item', item.id], item)
  patchInfoItemCaches(client, item.id, item)
}
