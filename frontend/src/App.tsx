import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { Layout } from './components/Layout'
import { getToken } from './lib/api'
import { AccountsPage } from './pages/Accounts'
import { AgentDetailPage } from './pages/AgentDetail'
import { AgentsPage } from './pages/Agents'
import { DashboardPage } from './pages/Dashboard'
import { KeysPage } from './pages/Keys'
import { BenchmarkPage } from './pages/Benchmark'
import { BenchmarkHistoryPage } from './pages/BenchmarkHistory'
import { LogDetailPage } from './pages/LogDetail'
import { LoginPage } from './pages/Login'
import { LogsPage } from './pages/Logs'
import { SharePage } from './pages/Share'
import type { ReactElement } from 'react'

const queryClient = new QueryClient()

function Guard({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Toaster theme="dark" position="top-center" richColors closeButton />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/share" element={<SharePage />} />
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
            <Route path="/benchmark" element={<BenchmarkPage />} />
            <Route path="/benchmark/history" element={<BenchmarkHistoryPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/logs/:id" element={<LogDetailPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
