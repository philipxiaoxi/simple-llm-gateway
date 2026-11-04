import { Monitor, Moon, Sun } from 'lucide-react'
import type { ThemeMode } from '../lib/api'
import { useThemePreference } from '../lib/useThemePreference'
import { cn } from '../lib/utils'

const OPTIONS: { value: ThemeMode; label: string; hint: string; icon: typeof Sun }[] = [
  { value: 'system', label: '跟随系统', hint: '随设备外观自动切换', icon: Monitor },
  { value: 'light', label: '浅色', hint: '始终使用浅色主题', icon: Sun },
  { value: 'dark', label: '深色', hint: '始终使用深色主题', icon: Moon },
]

/** 设置里的主题选项：三态模式，写入服务端后其它设备登录即可同步。 */
export function ThemePreferencePanel() {
  const { mode, theme, choose, saving } = useThemePreference()

  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="主题" className="grid gap-2 sm:grid-cols-3">
        {OPTIONS.map((option) => {
          const active = mode === option.value
          const Icon = option.icon
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={saving}
              onClick={() => choose(option.value)}
              className={cn(
                'flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition disabled:opacity-60',
                active
                  ? 'border-signal/50 bg-signal/10 text-paper'
                  : 'border-line bg-panel-2 text-mist hover:border-line-strong hover:text-paper',
              )}
            >
              <Icon size={16} className={cn('mt-0.5 shrink-0', active ? 'text-signal' : 'text-mist-soft')} />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{option.label}</span>
                <span className="mt-0.5 block text-[11px] leading-4 text-mist-soft">{option.hint}</span>
              </span>
            </button>
          )
        })}
      </div>
      <p className="text-xs text-mist-soft">
        {mode === 'system'
          ? `当前跟随系统，正在使用${theme === 'dark' ? '深色' : '浅色'}主题。`
          : '设置会保存到账号，换设备登录后自动同步。'}
      </p>
    </div>
  )
}
