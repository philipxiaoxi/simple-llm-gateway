import {
  Activity,
  BookOpen,
  Download,
  FileText,
  Film,
  Gauge,
  History,
  Globe,
  Image,
  KeyRound,
  MessageSquareText,
  Mic,
  Newspaper,
  PackageOpen,
  RadioTower,
  ScrollText,
  ServerCog,
  Shapes,
  Shield,
  ShieldCheck,
  Shirt,
  Sparkles,
  Store,
  Timer,
  Trophy,
  UserRound,
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
      { to: '/mcp-plaza', label: 'MCP 广场', icon: Store, keywords: 'mcp 广场 能力 服务目录', perm: 'mcp_catalog:view' },
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

/** 广场能力页：不进侧栏，但全局搜索（⌘K / 抽屉）能直接跳转。 */
export const PLAZA_APP_LINKS: NavLink[] = [
  { to: '/mcp-plaza/knowledge', label: '知识库', icon: BookOpen, keywords: 'mcp knowledge 知识库 检索', perm: 'mcp_knowledge:view' },
  { to: '/mcp-plaza/docparse', label: '文档转 Markdown', icon: FileText, keywords: 'mcp docparse 文档 解析 markdown', perm: 'mcp_docparse:view' },
  { to: '/mcp-plaza/sites', label: '站点部署', icon: Globe, keywords: 'mcp site 站点 部署 预览', perm: 'mcp_sites:view' },
  { to: '/mcp-plaza/douyin', label: '抖音视频下载', icon: Download, keywords: 'mcp douyin 抖音 下载', perm: 'mcp_douyin:view' },
  { to: '/mcp-plaza/portraits', label: '人像参考库', icon: Image, keywords: 'mcp portrait 人像 参考图 发型', perm: 'mcp_portraits:view' },
  { to: '/mcp-plaza/poses', label: '姿势参考库', icon: UserRound, keywords: 'mcp pose 姿势 参考图', perm: 'mcp_poses:view' },
  { to: '/mcp-plaza/costumes', label: '服装参考库', icon: Shirt, keywords: 'mcp costume 服装 穿搭 参考图', perm: 'mcp_costumes:view' },
  { to: '/mcp-plaza/diagrams', label: '图表生成', icon: Shapes, keywords: 'mcp diagram 图表 archify', perm: 'mcp_diagrams:view' },
  { to: '/mcp-plaza/generation', label: '图片 / 视频生成配置', icon: Sparkles, keywords: 'mcp generation 生成 供应商 模型', perm: 'generation:settings' },
  { to: '/mcp-plaza/info-report', label: '资讯上报', icon: Newspaper, keywords: 'mcp info 资讯 上报', perm: 'mcp_info:view' },
  { to: '/mcp-plaza/docs', label: 'MCP 接入说明', icon: ScrollText, keywords: 'mcp docs 接入 文档', perm: 'mcp_catalog:view' },
  { to: '/mcp-plaza/keys', label: 'MCP Key', icon: KeyRound, keywords: 'mcp key 密钥 令牌', perm: 'mcp_keys:view' },
  { to: '/mcp-plaza/calls', label: 'MCP 调用记录', icon: ScrollText, keywords: 'mcp calls 调用 日志', perm: 'mcp_calls:view' },
]

export const SEARCH_LINKS: NavLink[] = [...NAV_LINKS, ...PLAZA_APP_LINKS]

/** 移动端底部 Tab 的内置默认；用户未保存个人配置且管理员未设默认时使用。 */
export const BUILTIN_TAB_PATHS = ['/', '/accounts', '/keys', '/logs', '/skills']

export const TAB_LINKS: NavLink[] = [
  { to: '/', label: '概览', icon: Activity, perm: 'dashboard:view' },
  { to: '/accounts', label: '账号', icon: RadioTower, perm: 'accounts:view' },
  { to: '/keys', label: 'Key', icon: KeyRound, perm: 'keys:view' },
  { to: '/logs', label: '记录', icon: MessageSquareText, perm: 'logs:view' },
  { to: '/skills', label: 'Skills', icon: Sparkles, perm: 'skills:view' },
]

export function linksForPaths(paths: string[], pool: NavLink[] = SEARCH_LINKS): NavLink[] {
  const byPath = new Map(pool.map((link) => [link.to, link]))
  const picked: NavLink[] = []
  for (const path of paths) {
    const link = byPath.get(path)
    if (link && !picked.some((item) => item.to === link.to)) picked.push(link)
  }
  return picked
}

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
  const ranked = [...SEARCH_LINKS].sort((left, right) => right.to.length - left.to.length)
  const match = ranked.find((link) => isPathActive(link.to, pathname))
  return match?.label ?? '控制台'
}
