import { useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getToken, api, type Preferences, type ThemeMode } from './api'
import { errorMessage } from './utils'
import { getThemeMode, setThemeMode, useTheme, useThemeMode } from './theme'
import { notifyBad } from './toast'

const QUERY_KEY = ['preferences'] as const

/**
 * 主题偏好：本地立即生效，同时写入服务端，登录后由 ThemeSync 拉回最新值。
 * 切换失败时回滚到切换前的模式，避免界面和服务端长期不一致。
 */
export function useThemePreference() {
  const queryClient = useQueryClient()
  const mode = useThemeMode()
  const theme = useTheme()

  const mutation = useMutation({
    mutationFn: (next: ThemeMode) => api.savePreferences({ theme: next }),
    onSuccess: (saved) => {
      queryClient.setQueryData<Preferences>(QUERY_KEY, saved)
    },
    onError: (error) => {
      notifyBad(errorMessage(error, '主题偏好保存失败'))
    },
  })

  function choose(next: ThemeMode) {
    if (next === getThemeMode()) return
    const previous = getThemeMode()
    setThemeMode(next)
    mutation.mutate(next, {
      onError: () => setThemeMode(previous, { persistLocal: true }),
    })
  }

  return { mode, theme, choose, saving: mutation.isPending }
}

/** 读取服务端偏好并应用到本地；未登录时不发请求。 */
export function ThemeSync() {
  const hasToken = Boolean(getToken())
  const { data } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.preferences,
    enabled: hasToken,
    staleTime: 5 * 60_000,
    retry: false,
  })

  const serverMode = data?.theme
  useEffect(() => {
    if (!serverMode || serverMode === getThemeMode()) return
    setThemeMode(serverMode, { persistLocal: true })
  }, [serverMode])

  return null
}
