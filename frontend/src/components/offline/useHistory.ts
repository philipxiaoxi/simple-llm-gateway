import { useCallback, useState } from 'react'

/** 输入历史：最多保留 N 条，去重后置顶，存 localStorage。 */
export function useHistory(key: string, limit = 10) {
  const storageKey = `offline:history:${key}`
  const [history, setHistory] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      const parsed = raw ? (JSON.parse(raw) as unknown) : []
      return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []
    } catch {
      return []
    }
  })

  const remember = useCallback(
    (value: string) => {
      const trimmed = value.trim()
      if (!trimmed) return
      setHistory((prev) => {
        const next = [trimmed, ...prev.filter((item) => item !== trimmed)].slice(0, limit)
        try {
          localStorage.setItem(storageKey, JSON.stringify(next))
        } catch {
          /* ignore */
        }
        return next
      })
    },
    [limit, storageKey],
  )

  return { history, remember }
}
