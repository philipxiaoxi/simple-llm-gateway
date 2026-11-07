import { lazy, Suspense } from 'react'

import { useAssistant } from './AssistantProvider'

const AssistantPanel = lazy(() =>
  import('./AssistantPanel').then((module) => ({ default: module.AssistantPanel })),
)

export function AssistantPanelGate() {
  const { open } = useAssistant()
  if (!open) return null
  return (
    <Suspense fallback={null}>
      <AssistantPanel />
    </Suspense>
  )
}
