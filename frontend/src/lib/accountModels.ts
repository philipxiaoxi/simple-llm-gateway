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
  { border: 'border-[#a78bfa]/35', dot: 'bg-[#a78bfa]', text: 'text-[#a78bfa]', tint: 'bg-[#a78bfa]/5', hover: 'hover:bg-[#a78bfa]/15' },
  { border: 'border-[#fb923c]/35', dot: 'bg-[#fb923c]', text: 'text-[#fb923c]', tint: 'bg-[#fb923c]/5', hover: 'hover:bg-[#fb923c]/15' },
  { border: 'border-[#f472b6]/35', dot: 'bg-[#f472b6]', text: 'text-[#f472b6]', tint: 'bg-[#f472b6]/5', hover: 'hover:bg-[#f472b6]/15' },
  { border: 'border-[#22d3ee]/35', dot: 'bg-[#22d3ee]', text: 'text-[#22d3ee]', tint: 'bg-[#22d3ee]/5', hover: 'hover:bg-[#22d3ee]/15' },
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
