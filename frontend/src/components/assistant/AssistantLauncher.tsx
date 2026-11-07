import { Sparkles } from 'lucide-react'

import { useAssistant } from './AssistantProvider'

export function AssistantLauncher() {
  const { open, setOpen, streaming } = useAssistant()
  if (open) return null
  return (
    <button
      type="button"
      aria-label="打开 AI 助手"
      title="AI 助手"
      onClick={() => setOpen(true)}
      className="group fixed bottom-[calc(var(--app-tab)+env(safe-area-inset-bottom)+12px)] right-4 z-40 flex h-14 w-14 items-center justify-center rounded-2xl border border-signal/35 bg-panel text-signal shadow-floating transition-all duration-200 hover:-translate-y-0.5 hover:border-signal/60 hover:shadow-glow-soft active:scale-95 lg:bottom-6 lg:right-6"
    >
      <span className="pointer-events-none absolute inset-0 rounded-2xl bg-signal/10 opacity-70 blur-md transition-opacity group-hover:opacity-100" aria-hidden="true" />
      <span className="relative flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-signal/25 via-panel-2 to-info/15 shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
        <Sparkles size={20} />
      </span>
      {streaming ? (
        <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal opacity-60" />
          <span className="relative inline-flex h-3.5 w-3.5 rounded-full border-2 border-panel bg-signal" />
        </span>
      ) : null}
    </button>
  )
}
