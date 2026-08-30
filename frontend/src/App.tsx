import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from './components/Layout'
import { PwaUpdater } from './components/PwaUpdate'
import { getToken } from './lib/api'
import { AccountsPage } from './pages/Accounts'
import { AgentDetailPage } from './pages/AgentDetail'
import { AgentsPage } from './pages/Agents'
import { DashboardPage } from './pages/Dashboard'
import { KeysPage } from './pages/Keys'
import { BenchmarkPage } from './pages/Benchmark'
import { BenchmarkHistoryPage } from './pages/BenchmarkHistory'
import { LeaderboardPage } from './pages/Leaderboard'
import { LogDetailPage } from './pages/LogDetail'
import { LoginPage } from './pages/Login'
import { LogsPage } from './pages/Logs'
import { PublicLeaderboardPage } from './pages/PublicLeaderboard'
import { SharePage } from './pages/Share'
import { SkillDetailPage } from './pages/SkillDetail'
import { SkillsPage } from './pages/Skills'
import { ContentAuditPage } from './pages/ContentAudit'
import { JobsPage } from './pages/Jobs'
import { ToolsPage } from './pages/Tools'
import type { ReactElement } from 'react'

const queryClient = new QueryClient()

function Guard({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PwaUpdater />
      <Toaster theme="dark" position="top-center" richColors closeButton />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share" element={<SharePage />} />
          <Route path="/share/leaderboard" element={<PublicLeaderboardPage />} />
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
            <Route path="/skills/:skillId" element={<SkillDetailPage />} />
            <Route path="/tools" element={<ToolsPage />} />
            <Route path="/benchmark" element={<BenchmarkPage />} />
            <Route path="/benchmark/history" element={<BenchmarkHistoryPage />} />
            <Route path="/leaderboard" element={<LeaderboardPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/logs/:id" element={<LogDetailPage />} />
            <Route path="/content-audit" element={<ContentAuditPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
