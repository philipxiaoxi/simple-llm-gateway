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
  ShieldAlert,
  Sparkles,
  Store,
  Timer,
  Trophy,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type NavLink = {
  to: string
  label: string
  icon: LucideIcon
  /** 命令面板 / 抽屉搜索的补充关键词 */
  keywords?: string
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
      { to: '/', label: '概览', icon: Activity, keywords: 'dashboard 首页 总览' },
      { to: '/accounts', label: '上游账号', icon: RadioTower, keywords: 'accounts 账号 上游' },
      { to: '/agents', label: '网关代理', icon: ServerCog, keywords: 'agent 代理 网关' },
      { to: '/keys', label: 'API Key', icon: KeyRound, keywords: 'key 密钥 令牌' },
      { to: '/logs', label: '记录审计', icon: MessageSquareText, keywords: 'logs 请求 日志 审计' },
    ],
  },
  {
    label: '模型',
    links: [
      { to: '/benchmark', label: '模型测速', icon: Gauge, keywords: 'benchmark 测速' },
      { to: '/benchmark/history', label: '测速历史', icon: History, keywords: 'benchmark history 历史' },
      { to: '/leaderboard', label: '模型榜', icon: Trophy, keywords: 'leaderboard 榜单' },
    ],
  },
  {
    label: '内容',
    links: [
      { to: '/info', label: '资讯收集', icon: Newspaper, keywords: 'info 资讯 采集' },
      { to: '/content-audit', label: '内容审计', icon: ShieldAlert, keywords: 'audit 内容 审核' },
      { to: '/image-gen', label: '图片生成', icon: Image, keywords: 'image 图片 生成' },
      { to: '/video-gen', label: '视频生成', icon: Film, keywords: 'video 视频 生成' },
      { to: '/offline', label: '离线下载', icon: PackageOpen, keywords: 'offline 下载' },
    ],
  },
  {
    label: '平台',
    links: [
      { to: '/skills', label: 'Skills', icon: Sparkles, keywords: 'skill 技能' },
      { to: '/mcp-plaza', label: 'MCP 广场', icon: Store, keywords: 'mcp 广场 能力' },
      { to: '/tools', label: '工具中心', icon: Download, keywords: 'tools 工具' },
      { to: '/jobs', label: '定时任务', icon: Timer, keywords: 'jobs 任务 定时' },
      { to: '/voice', label: '语音房', icon: Mic, keywords: 'voice 语音' },
    ],
  },
]

export const NAV_LINKS: NavLink[] = NAV_GROUPS.flatMap((group) => group.links)

/** 移动端底部 Tab：保留最高频入口，其余走抽屉。 */
export const TAB_LINKS: NavLink[] = [
  { to: '/', label: '概览', icon: Activity },
  { to: '/accounts', label: '账号', icon: RadioTower },
  { to: '/keys', label: 'Key', icon: KeyRound },
  { to: '/logs', label: '记录', icon: MessageSquareText },
  { to: '/skills', label: 'Skills', icon: Sparkles },
]

export function isPathActive(to: string, pathname: string) {
  if (to === '/') return pathname === '/'
  if (to === '/benchmark') return pathname === '/benchmark'
  return pathname === to || pathname.startsWith(`${to}/`)
}

export function currentPageLabel(pathname: string) {
  const ranked = [...NAV_LINKS].sort((left, right) => right.to.length - left.to.length)
  const match = ranked.find((link) => isPathActive(link.to, pathname))
  return match?.label ?? '控制台'
}
