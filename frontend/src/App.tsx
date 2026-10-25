import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from './components/Layout'
import { PwaUpdater } from './components/PwaUpdate'
import { getToken } from './lib/api'
import { RequirePerm, SessionProvider } from './lib/session'
import { DashboardPage } from './pages/Dashboard'
import { LoginPage } from './pages/Login'
import type { ReactElement } from 'react'

// 首屏只需要仪表盘和登录页，其余页面按路由懒加载，避免打进入口 chunk。
const AccountsPage = lazy(() => import('./pages/Accounts').then((m) => ({ default: m.AccountsPage })))
const AgentDetailPage = lazy(() => import('./pages/AgentDetail').then((m) => ({ default: m.AgentDetailPage })))
const AgentsPage = lazy(() => import('./pages/Agents').then((m) => ({ default: m.AgentsPage })))
const BenchmarkPage = lazy(() => import('./pages/Benchmark').then((m) => ({ default: m.BenchmarkPage })))
const BenchmarkHistoryPage = lazy(() =>
  import('./pages/BenchmarkHistory').then((m) => ({ default: m.BenchmarkHistoryPage })),
)
const JobsPage = lazy(() => import('./pages/Jobs').then((m) => ({ default: m.JobsPage })))
const InfoFeedPage = lazy(() => import('./pages/InfoFeed').then((m) => ({ default: m.InfoFeedPage })))
const InfoItemPage = lazy(() => import('./pages/InfoItem').then((m) => ({ default: m.InfoItemPage })))
const InfoSourcesPage = lazy(() => import('./pages/InfoSources').then((m) => ({ default: m.InfoSourcesPage })))
const ImageGenPage = lazy(() => import('./pages/ImageGen').then((m) => ({ default: m.ImageGenPage })))
const VideoGenPage = lazy(() => import('./pages/VideoGen').then((m) => ({ default: m.VideoGenPage })))
const KeysPage = lazy(() => import('./pages/Keys').then((m) => ({ default: m.KeysPage })))
const LeaderboardPage = lazy(() => import('./pages/Leaderboard').then((m) => ({ default: m.LeaderboardPage })))
const LogDetailPage = lazy(() => import('./pages/LogDetail').then((m) => ({ default: m.LogDetailPage })))
const LogsPage = lazy(() => import('./pages/Logs').then((m) => ({ default: m.LogsPage })))
const SecurityPage = lazy(() => import('./pages/Security').then((m) => ({ default: m.SecurityPage })))
const PublicLeaderboardPage = lazy(() =>
  import('./pages/PublicLeaderboard').then((m) => ({ default: m.PublicLeaderboardPage })),
)
const PublicInfoPage = lazy(() =>
  import('./pages/PublicInfo').then((m) => ({ default: m.PublicInfoPage })),
)
const PublicInfoItemPage = lazy(() =>
  import('./pages/PublicInfo').then((m) => ({ default: m.PublicInfoItemPage })),
)
const OfflinePage = lazy(() => import('./pages/Offline').then((m) => ({ default: m.OfflinePage })))
const PublicOfflinePage = lazy(() =>
  import('./pages/PublicOffline').then((m) => ({ default: m.PublicOfflinePage })),
)
const SharePage = lazy(() => import('./pages/Share').then((m) => ({ default: m.SharePage })))
const SkillBundleDetailPage = lazy(() =>
  import('./pages/SkillBundleDetail').then((m) => ({ default: m.SkillBundleDetailPage })),
)
const SkillBundlesPage = lazy(() => import('./pages/SkillBundles').then((m) => ({ default: m.SkillBundlesPage })))
const SkillDetailPage = lazy(() => import('./pages/SkillDetail').then((m) => ({ default: m.SkillDetailPage })))
const SkillsPage = lazy(() => import('./pages/Skills').then((m) => ({ default: m.SkillsPage })))
const ToolsPage = lazy(() => import('./pages/Tools').then((m) => ({ default: m.ToolsPage })))
const VoiceRoomsPage = lazy(() => import('./pages/VoiceRooms').then((m) => ({ default: m.VoiceRoomsPage })))
const VoiceRoomDetailPage = lazy(() =>
  import('./pages/VoiceRoomDetail').then((m) => ({ default: m.VoiceRoomDetailPage })),
)
// 手机端页面是公开的（不能要求管理员登录），单独懒加载，避免被管理端 chunk 牵连。
const VoiceJoinPage = lazy(() => import('./pages/VoiceJoin').then((m) => ({ default: m.VoiceJoinPage })))
const VoiceSendPage = lazy(() => import('./pages/VoiceSend').then((m) => ({ default: m.VoiceSendPage })))
const McpPlazaLayout = lazy(() => import('./pages/McpPlaza').then((m) => ({ default: m.McpPlazaLayout })))
const McpPlazaCatalogPage = lazy(() => import('./pages/McpPlaza').then((m) => ({ default: m.McpPlazaCatalogPage })))
const McpKnowledgePage = lazy(() => import('./pages/McpKnowledge').then((m) => ({ default: m.McpKnowledgePage })))
const McpKnowledgeDetailPage = lazy(() =>
  import('./pages/McpKnowledge').then((m) => ({ default: m.McpKnowledgeDetailPage })),
)
const McpKnowledgeJobsPage = lazy(() =>
  import('./pages/McpKnowledgeJobs').then((m) => ({ default: m.McpKnowledgeJobsPage })),
)
const McpKeysPage = lazy(() => import('./pages/McpKeys').then((m) => ({ default: m.McpKeysPage })))
const McpCallsPage = lazy(() => import('./pages/McpCalls').then((m) => ({ default: m.McpCallsPage })))
const McpDocsPage = lazy(() => import('./pages/McpDocs').then((m) => ({ default: m.McpDocsPage })))
const McpDocParsePage = lazy(() => import('./pages/McpDocParse').then((m) => ({ default: m.McpDocParsePage })))
const McpSitesPage = lazy(() => import('./pages/McpSites').then((m) => ({ default: m.McpSitesPage })))
const McpSiteDetailPage = lazy(() =>
  import('./pages/McpSiteDetail').then((m) => ({ default: m.McpSiteDetailPage })),
)
const McpDouyinPage = lazy(() => import('./pages/McpDouyin').then((m) => ({ default: m.McpDouyinPage })))
const McpGenerationPage = lazy(() => import('./pages/McpGeneration').then((m) => ({ default: m.McpGenerationPage })))
const McpDouyinDetailPage = lazy(() =>
  import('./pages/McpDouyinDetail').then((m) => ({ default: m.McpDouyinDetailPage })),
)
const McpDouyinSharePage = lazy(() =>
  import('./pages/McpDouyinShare').then((m) => ({ default: m.McpDouyinSharePage })),
)
const McpDiagramsPage = lazy(() =>
  import('./pages/McpDiagrams').then((m) => ({ default: m.McpDiagramsPage })),
)
const McpDiagramDetailPage = lazy(() =>
  import('./pages/McpDiagramDetail').then((m) => ({ default: m.McpDiagramDetailPage })),
)
const UsersPage = lazy(() => import('./pages/Users').then((m) => ({ default: m.UsersPage })))
const RolesPage = lazy(() => import('./pages/Roles').then((m) => ({ default: m.RolesPage })))

/** 页面 -> 进入所需权限（与 components/nav.ts 的 perm 对应） */
const PAGE_PERMS: { pattern: string; perm: string }[] = [
  { pattern: '/', perm: 'dashboard:view' },
  { pattern: '/accounts', perm: 'accounts:view' },
  { pattern: '/agents', perm: 'agents:view' },
  { pattern: '/keys', perm: 'keys:view' },
  { pattern: '/logs', perm: 'logs:view' },
  { pattern: '/benchmark/history', perm: 'benchmark_history:view' },
  { pattern: '/benchmark', perm: 'benchmark:view' },
  { pattern: '/leaderboard', perm: 'leaderboard:view' },
  { pattern: '/info/sources', perm: 'info:view' },
  { pattern: '/info', perm: 'info:view' },
  { pattern: '/image-gen', perm: 'image_gen:view' },
  { pattern: '/video-gen', perm: 'video_gen:view' },
  { pattern: '/offline', perm: 'offline:view' },
  { pattern: '/skills/bundles', perm: 'skill_bundles:view' },
  { pattern: '/skills', perm: 'skills:view' },
  { pattern: '/mcp-plaza/knowledge', perm: 'mcp_knowledge:view' },
  { pattern: '/mcp-plaza/docparse', perm: 'mcp_docparse:view' },
  { pattern: '/mcp-plaza/sites', perm: 'mcp_sites:view' },
  { pattern: '/mcp-plaza/douyin', perm: 'mcp_douyin:view' },
  { pattern: '/mcp-plaza/diagrams', perm: 'mcp_diagrams:view' },
  { pattern: '/mcp-plaza/generation', perm: 'mcp_generation:view' },
  { pattern: '/mcp-plaza/keys', perm: 'mcp_keys:view' },
  { pattern: '/mcp-plaza/calls', perm: 'mcp_calls:view' },
  { pattern: '/mcp-plaza', perm: 'mcp_catalog:view' },
  { pattern: '/tools', perm: 'tools:view' },
  { pattern: '/jobs', perm: 'jobs:view' },
  { pattern: '/security', perm: 'security:view' },
  { pattern: '/voice', perm: 'voice:view' },
  { pattern: '/users', perm: 'users:view' },
  { pattern: '/roles', perm: 'roles:view' },
]

const DETAIL_PERMS: { test: (path: string) => boolean; perm: string }[] = [
  { test: (path) => /^\/logs\/[^/]+$/.test(path), perm: 'logs:detail' },
]

function permForPath(pathname: string): string | undefined {
  const detail = DETAIL_PERMS.find((entry) => entry.test(pathname))
  if (detail) return detail.perm
  const ranked = [...PAGE_PERMS].sort((left, right) => right.pattern.length - left.pattern.length)
  return ranked.find((entry) =>
    entry.pattern === '/' ? pathname === '/' : pathname === entry.pattern || pathname.startsWith(`${entry.pattern}/`),
  )?.perm
}

function Guarded({ children }: { children: ReactElement }) {
  const perm = permForPath(useLocation().pathname)
  if (!perm) return children
  return <RequirePerm code={perm}>{children}</RequirePerm>
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 30s 内复用缓存，避免每次挂载/切页都重新请求。
      staleTime: 30_000,
      // 切回标签页不再全量刷新，交由各页面的 refetchInterval 控制实时性。
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function Guard({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return children
}

// 懒加载期间的兜底画面，沿用启动画面（#boot-splash）的深色样式，避免白屏闪烁。
function RouteFallback() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink" aria-hidden="true">
      <div className="h-7 w-7 animate-spin rounded-full border-[3px] border-signal/20 border-t-signal" />
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PwaUpdater />
      <Toaster
        theme="dark"
        position="top-center"
        richColors
        closeButton
        // 顶部提示避开 iOS 安全区（灵动岛/刘海），否则会被灵动岛挡住
        mobileOffset={{ top: 'calc(env(safe-area-inset-top) + 12px)' }}
      />
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/share" element={<SharePage />} />
            <Route path="/share/leaderboard" element={<PublicLeaderboardPage />} />
            <Route path="/share/info" element={<PublicInfoPage />}>
              <Route path=":itemId" element={<PublicInfoItemPage />} />
            </Route>
            <Route path="/share/offline" element={<PublicOfflinePage />} />
            <Route path="/share/douyin" element={<McpDouyinSharePage />} />
            <Route path="/voice/join" element={<VoiceJoinPage />} />
            <Route path="/voice/send/:roomId" element={<VoiceSendPage />} />
            <Route
              element={
                <Guard>
                  <SessionProvider>
                    <Guarded>
                      <Layout />
                    </Guarded>
                  </SessionProvider>
                </Guard>
              }
            >
              <Route path="/" element={<DashboardPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route path="/agents/:agentId" element={<AgentDetailPage />} />
              <Route path="/keys" element={<KeysPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/skills/bundles" element={<SkillBundlesPage />} />
              <Route path="/skills/bundles/:bundleId" element={<SkillBundleDetailPage />} />
              <Route path="/skills/:skillId" element={<SkillDetailPage />} />
              <Route path="/mcp-plaza" element={<McpPlazaLayout />}>
                <Route index element={<McpPlazaCatalogPage />} />
                <Route path="knowledge" element={<McpKnowledgePage />} />
                <Route path="knowledge/jobs" element={<McpKnowledgeJobsPage />} />
                <Route path="knowledge/:kbId" element={<McpKnowledgeDetailPage />} />
                <Route path="docparse" element={<McpDocParsePage />} />
                <Route path="sites" element={<McpSitesPage />} />
                <Route path="sites/:siteId" element={<McpSiteDetailPage />} />
                <Route path="douyin" element={<McpDouyinPage />} />
                <Route path="douyin/:jobId" element={<McpDouyinDetailPage />} />
                <Route path="diagrams" element={<McpDiagramsPage />} />
                <Route path="diagrams/:siteId" element={<McpDiagramDetailPage />} />
                <Route path="generation" element={<McpGenerationPage />} />
                <Route path="keys" element={<McpKeysPage />} />
                <Route path="calls" element={<McpCallsPage />} />
                <Route path="docs" element={<McpDocsPage />} />
              </Route>
              <Route path="/tools" element={<ToolsPage />} />
              {/* 资讯收集：详情是浮层，挂在 /info 子路由上，瀑布流保持挂载（返回即回到原滚动位置） */}
              <Route path="/info" element={<InfoFeedPage />}>
                <Route path=":itemId" element={<InfoItemPage />} />
              </Route>
              <Route path="/info/sources" element={<InfoSourcesPage />} />
              <Route path="/image-gen" element={<ImageGenPage />} />
              <Route path="/video-gen" element={<VideoGenPage />} />
              <Route path="/offline" element={<OfflinePage />} />
              <Route path="/benchmark" element={<BenchmarkPage />} />
              <Route path="/benchmark/history" element={<BenchmarkHistoryPage />} />
              <Route path="/leaderboard" element={<LeaderboardPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/logs/:id" element={<LogDetailPage />} />
              <Route path="/security" element={<SecurityPage />} />
              <Route path="/voice" element={<VoiceRoomsPage />} />
              <Route path="/voice/rooms/:roomId" element={<VoiceRoomDetailPage />} />
              <Route path="/users" element={<UsersPage />} />
              <Route path="/roles" element={<RolesPage />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
