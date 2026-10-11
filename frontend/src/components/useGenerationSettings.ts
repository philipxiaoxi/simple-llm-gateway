import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export const GENERATION_SETTINGS_KEY = ['generation-settings'] as const

export function useGenerationSettings(enabled = true) {
  return useQuery({ queryKey: GENERATION_SETTINGS_KEY, queryFn: api.generationSettings, enabled })
}
