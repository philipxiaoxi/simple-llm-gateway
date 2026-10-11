import type { Account } from './api'

export type Target = { account: Account; model: string; enabled: boolean }

export function keyOf(target: Target) {
  return `${target.account.id}:${target.model}`
}

export function targetsFromAccounts(accounts: Account[]): Target[] {
  return accounts
    .filter((account) => account.status === 'active')
    .flatMap((account) =>
      account.models.map((model) => ({ account, model: model.id, enabled: model.enabled !== false })),
    )
}
