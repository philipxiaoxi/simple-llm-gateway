import type { ShareLookup, ShareModelEntry } from './api'

export type AccountColor = {
  border: string
  dot: string
  text: string
  tint: string
  hover: string
}

const ACCOUNT_COLORS: AccountColor[] = [
  { border: 'border-signal/35', dot: 'bg-signal', text: 'text-signal', tint: 'bg-signal/5', hover: 'hover:bg-signal/15' },
  { border: 'border-info/35', dot: 'bg-info', text: 'text-info', tint: 'bg-info/5', hover: 'hover:bg-info/15' },
  { border: 'border-accent-violet/35', dot: 'bg-accent-violet', text: 'text-accent-violet', tint: 'bg-accent-violet/5', hover: 'hover:bg-accent-violet/15' },
  { border: 'border-accent-orange/35', dot: 'bg-accent-orange', text: 'text-accent-orange', tint: 'bg-accent-orange/5', hover: 'hover:bg-accent-orange/15' },
  { border: 'border-accent-pink/35', dot: 'bg-accent-pink', text: 'text-accent-pink', tint: 'bg-accent-pink/5', hover: 'hover:bg-accent-pink/15' },
  { border: 'border-accent-cyan/35', dot: 'bg-accent-cyan', text: 'text-accent-cyan', tint: 'bg-accent-cyan/5', hover: 'hover:bg-accent-cyan/15' },
]

export function accountColor(index: number) {
  return ACCOUNT_COLORS[Math.max(0, index) % ACCOUNT_COLORS.length]
}

export function shareModelEntries(lookup: ShareLookup): ShareModelEntry[] {
  if (lookup.model_entries?.length) return lookup.model_entries
  const account = lookup.accounts[0]
  return lookup.models.map((id) => ({
    id,
    raw_id: id,
    account_id: account?.id ?? 0,
    account_name: account?.name ?? lookup.account_name ?? '可用模型',
    account_source: account?.source ?? lookup.account_source,
    provider: account?.provider ?? lookup.provider,
    account_index: 0,
  }))
}
