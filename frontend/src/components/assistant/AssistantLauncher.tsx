import { Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type PointerEvent } from 'react'

import { useMediaQuery } from '../../lib/useMediaQuery'
import { cn } from '../../lib/utils'
import { Button } from '../ui'
import { useAssistant } from './AssistantProvider'

const HOLD_MS = 350
const MOVE_THRESHOLD = 8
type Position = { x: number; y: number }

function readPosition(key: string): Position | null {
  try {
    const position = JSON.parse(localStorage.getItem(key) ?? 'null')
    if (position && [position.x, position.y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
      return position
    }
  } catch {
    // 存储不可用或数据损坏时，使用默认位置。
  }
  return null
}

export function AssistantLauncher() {
  const { open, setOpen, streaming } = useAssistant()
  const desktop = useMediaQuery('(min-width: 1024px)')
  if (open) return null
  const storageKey = `pivot:assistant-launcher:${desktop ? 'desktop' : 'mobile'}`
  return <DraggableLauncher key={storageKey} storageKey={storageKey} streaming={streaming} onOpen={() => setOpen(true)} />
}

function DraggableLauncher({ storageKey, streaming, onOpen }: { storageKey: string; streaming: boolean; onOpen: () => void }) {
  const boundsRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState(() => readPosition(storageKey))
  const [draft, setDraft] = useState<Position | null>(null)
  const [dragging, setDragging] = useState(false)
  const gesture = useRef<{
    id: number
    start: Position
    offset: Position
    phase: 'pending' | 'dragging' | 'cancelled'
    timer: number
    next: Position | null
  } | null>(null)
  const suppressClick = useRef(false)

  useEffect(() => () => window.clearTimeout(gesture.current?.timer), [])

  function start(event: PointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || event.button !== 0 || gesture.current) return
    const rect = event.currentTarget.getBoundingClientRect()
    suppressClick.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
    gesture.current = {
      id: event.pointerId,
      start: { x: event.clientX, y: event.clientY },
      offset: { x: event.clientX - rect.left, y: event.clientY - rect.top },
      phase: 'pending',
      next: null,
      timer: window.setTimeout(() => {
        if (gesture.current?.phase !== 'pending') return
        gesture.current.phase = 'dragging'
        suppressClick.current = true
        setDragging(true)
      }, HOLD_MS),
    }
  }

  function move(event: PointerEvent<HTMLButtonElement>) {
    const current = gesture.current
    const bounds = boundsRef.current?.getBoundingClientRect()
    if (!current || current.id !== event.pointerId || !bounds) return
    if (current.phase === 'pending' && Math.hypot(event.clientX - current.start.x, event.clientY - current.start.y) > MOVE_THRESHOLD) {
      window.clearTimeout(current.timer)
      current.phase = 'cancelled'
      suppressClick.current = true
    }
    if (current.phase !== 'dragging') return
    const ratio = (offset: number, range: number) => range <= 0 ? 0 : Math.max(0, Math.min(1, offset / range))
    current.next = {
      x: ratio(event.clientX - bounds.left - current.offset.x, bounds.width - event.currentTarget.offsetWidth),
      y: ratio(event.clientY - bounds.top - current.offset.y, bounds.height - event.currentTarget.offsetHeight),
    }
    setDraft(current.next)
  }

  function finish(event: PointerEvent<HTMLButtonElement>) {
    const current = gesture.current
    if (!current || current.id !== event.pointerId) return
    window.clearTimeout(current.timer)
    gesture.current = null
    setDragging(false)
    setDraft(null)
    if (event.type === 'pointerup' && current.next) {
      setPosition(current.next)
      try {
        localStorage.setItem(storageKey, JSON.stringify(current.next))
      } catch {
        // 当前页面仍然使用拖动后的位置。
      }
    }
  }

  const placed = draft ?? position ?? { x: 1, y: 1 }
  return (
    <div
      ref={boundsRef}
      className="pointer-events-none fixed inset-x-4 top-[calc(var(--app-header)+var(--safe-top)+12px)] bottom-[calc(var(--app-tab)+env(safe-area-inset-bottom)+12px)] z-40 [--assistant-size:3.5rem] lg:inset-6"
    >
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label="打开 AI 助手"
        title="AI 助手（长按可拖动）"
        onClick={(event) => {
          if (event.detail === 0 || !suppressClick.current) onOpen()
          suppressClick.current = false
        }}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
        onLostPointerCapture={finish}
        onContextMenu={(event) => event.preventDefault()}
        style={{
          left: `calc(${placed.x * 100}% - var(--assistant-size) * ${placed.x})`,
          top: `calc(${placed.y * 100}% - var(--assistant-size) * ${placed.y})`,
        }}
        className={cn(
          'group pointer-events-auto absolute h-[var(--assistant-size)] w-[var(--assistant-size)] touch-none select-none rounded-2xl border border-signal/35 bg-panel text-signal shadow-floating hover:border-signal/60 hover:bg-panel hover:shadow-glow-soft active:scale-100 md:h-[var(--assistant-size)] md:w-[var(--assistant-size)]',
          dragging ? 'cursor-grabbing ring-2 ring-signal/50' : 'cursor-grab',
        )}
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
      </Button>
    </div>
  )
}
