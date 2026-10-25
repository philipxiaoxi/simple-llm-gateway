import {
  Activity,
  Download,
  Film,
  Gauge,
  History,
  Image,
  KeyRound,
  MessageSquareText,
  Mic,
  Newspaper,
  PackageOpen,
  RadioTower,
  ServerCog,
  Shield,
  ShieldCheck,
  Sparkles,
  Store,
  Timer,
  Trophy,
  Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type NavLink = {
  to: string
  label: string
  icon: LucideIcon
  /** 命令面板 / 抽屉搜索的补充关键词 */
  keywords?: string
  /** 进入该页面所需的权限码；缺省表示任何已登录账号可见 */
  perm?: string
}

export type NavGroup = {
  label: string
  links: NavLink[]
}

/** 侧栏分组：PC 分组折叠、平板图标轨、移动抽屉与 ⌘K 共用同一份配置。 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: '网关',
    links: [
      { to: '/', label: '概览', icon: Activity, keywords: 'dashboard 首页 总览', perm: 'dashboard:view' },
      { to: '/accounts', label: '上游账号', icon: RadioTower, keywords: 'accounts 账号 上游', perm: 'accounts:view' },
      { to: '/agents', label: '网关代理', icon: ServerCog, keywords: 'agent 代理 网关', perm: 'agents:view' },
      { to: '/keys', label: 'API Key', icon: KeyRound, keywords: 'key 密钥 令牌', perm: 'keys:view' },
      { to: '/logs', label: '记录审计', icon: MessageSquareText, keywords: 'logs 请求 日志 审计', perm: 'logs:view' },
    ],
  },
  {
    label: '模型',
    links: [
      { to: '/benchmark', label: '模型测速', icon: Gauge, keywords: 'benchmark 测速', perm: 'benchmark:view' },
      { to: '/benchmark/history', label: '测速历史', icon: History, keywords: 'benchmark history 历史', perm: 'benchmark_history:view' },
      { to: '/leaderboard', label: '模型榜', icon: Trophy, keywords: 'leaderboard 榜单', perm: 'leaderboard:view' },
    ],
  },
  {
    label: '内容',
    links: [
      { to: '/info', label: '资讯收集', icon: Newspaper, keywords: 'info 资讯 采集', perm: 'info:view' },
      { to: '/image-gen', label: '图片生成', icon: Image, keywords: 'image 图片 生成', perm: 'image_gen:view' },
      { to: '/video-gen', label: '视频生成', icon: Film, keywords: 'video 视频 生成', perm: 'video_gen:view' },
      { to: '/offline', label: '离线下载', icon: PackageOpen, keywords: 'offline 下载', perm: 'offline:view' },
    ],
  },
  {
    label: '平台',
    links: [
      { to: '/skills', label: 'Skills', icon: Sparkles, keywords: 'skill 技能', perm: 'skills:view' },
      { to: '/security', label: '安全中心', icon: Shield, keywords: 'security 安全 审计 登录 异常', perm: 'security:view' },
      { to: '/mcp-plaza', label: 'MCP 广场', icon: Store, keywords: 'mcp 广场 能力', perm: 'mcp_catalog:view' },
      { to: '/tools', label: '工具中心', icon: Download, keywords: 'tools 工具', perm: 'tools:view' },
      { to: '/jobs', label: '定时任务', icon: Timer, keywords: 'jobs 任务 定时', perm: 'jobs:view' },
      { to: '/voice', label: '语音房', icon: Mic, keywords: 'voice 语音', perm: 'voice:view' },
    ],
  },
  {
    label: '系统',
    links: [
      { to: '/users', label: '用户管理', icon: Users, keywords: 'user 用户 账号 管理员', perm: 'users:view' },
      { to: '/roles', label: '角色管理', icon: ShieldCheck, keywords: 'role 角色 权限', perm: 'roles:view' },
    ],
  },
]

/** 按权限过滤导航（侧栏、移动 Tab、命令面板共用）。 */
export function filterNavLinks<T extends { perm?: string }>(links: T[], can: (code?: string) => boolean): T[] {
  return links.filter((link) => can(link.perm))
}

export function filterNavGroups(groups: NavGroup[], can: (code?: string) => boolean): NavGroup[] {
  return groups
    .map((group) => ({ ...group, links: filterNavLinks(group.links, can) }))
    .filter((group) => group.links.length > 0)
}

export const NAV_LINKS: NavLink[] = NAV_GROUPS.flatMap((group) => group.links)

/** 移动端底部 Tab：保留最高频入口，其余走抽屉。 */
export const TAB_LINKS: NavLink[] = [
  { to: '/', label: '概览', icon: Activity, perm: 'dashboard:view' },
  { to: '/accounts', label: '账号', icon: RadioTower, perm: 'accounts:view' },
  { to: '/keys', label: 'Key', icon: KeyRound, perm: 'keys:view' },
  { to: '/logs', label: '记录', icon: MessageSquareText, perm: 'logs:view' },
  { to: '/skills', label: 'Skills', icon: Sparkles, perm: 'skills:view' },
]

export function isPathActive(to: string, pathname: string) {
  if (to === '/') return pathname === '/'
  if (to === '/benchmark') return pathname === '/benchmark'
  return pathname === to || pathname.startsWith(`${to}/`)
}

/**
 * 当前账号有权限访问的第一个页面（按导航顺序，默认落到「概览」）。
 * 用于：访问无权限页面时自动跳转，而不是展示无权限页。
 */
export function firstAccessiblePath(can: (code?: string) => boolean): string | null {
  return NAV_LINKS.find((link) => can(link.perm))?.to ?? null
}

export function currentPageLabel(pathname: string) {
  const ranked = [...NAV_LINKS].sort((left, right) => right.to.length - left.to.length)
  const match = ranked.find((link) => isPathActive(link.to, pathname))
  return match?.label ?? '控制台'
}
