import { useCan } from '../../lib/session'
import { AssistantLauncher } from './AssistantLauncher'
import { AssistantPanelGate } from './AssistantPanelGate'
import { AssistantProvider } from './AssistantProvider'

export function AssistantRoot() {
  const canUse = useCan('assistant:view')
  if (!canUse) return null
  return (
    <AssistantProvider>
      <AssistantLauncher />
      <AssistantPanelGate />
    </AssistantProvider>
  )
}
