import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from './components/Layout'
import { PwaUpdater } from './components/PwaUpdate'
import { getToken } from './lib/api'
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
const ContentAuditPage = lazy(() => import('./pages/ContentAudit').then((m) => ({ default: m.ContentAuditPage })))
const JobsPage = lazy(() => import('./pages/Jobs').then((m) => ({ default: m.JobsPage })))
const KeysPage = lazy(() => import('./pages/Keys').then((m) => ({ default: m.KeysPage })))
const LeaderboardPage = lazy(() => import('./pages/Leaderboard').then((m) => ({ default: m.LeaderboardPage })))
const LogDetailPage = lazy(() => import('./pages/LogDetail').then((m) => ({ default: m.LogDetailPage })))
const LogsPage = lazy(() => import('./pages/Logs').then((m) => ({ default: m.LogsPage })))
const PublicLeaderboardPage = lazy(() =>
  import('./pages/PublicLeaderboard').then((m) => ({ default: m.PublicLeaderboardPage })),
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
      <Toaster theme="dark" position="top-center" richColors closeButton />
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/share" element={<SharePage />} />
            <Route path="/share/leaderboard" element={<PublicLeaderboardPage />} />
            <Route path="/voice/join" element={<VoiceJoinPage />} />
            <Route path="/voice/send/:roomId" element={<VoiceSendPage />} />
            <Route
              element={
                <Guard>
                  <Layout />
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
                <Route path="keys" element={<McpKeysPage />} />
                <Route path="calls" element={<McpCallsPage />} />
                <Route path="docs" element={<McpDocsPage />} />
              </Route>
              <Route path="/tools" element={<ToolsPage />} />
              <Route path="/benchmark" element={<BenchmarkPage />} />
              <Route path="/benchmark/history" element={<BenchmarkHistoryPage />} />
              <Route path="/leaderboard" element={<LeaderboardPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/logs/:id" element={<LogDetailPage />} />
              <Route path="/content-audit" element={<ContentAuditPage />} />
              <Route path="/voice" element={<VoiceRoomsPage />} />
              <Route path="/voice/rooms/:roomId" element={<VoiceRoomDetailPage />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
