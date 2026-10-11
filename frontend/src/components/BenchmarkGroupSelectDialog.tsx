import { useState } from 'react'
import { Badge, Button, Dialog, Input } from './ui'
import type { BenchmarkGroup } from '../lib/api'

export function BenchmarkGroupSelectDialog({
  groups,
  currentTargetCount,
  activeGroupId,
  onApply,
  onSaveNew,
  onManage,
  onClose,
}: {
  groups: BenchmarkGroup[]
  currentTargetCount: number
  activeGroupId: number | null
  onApply: (groupId: number) => void
  onSaveNew: (name: string) => void
  onManage: () => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  return (
    <Dialog title="选择测速分组" onClose={onClose}>
      <div className="space-y-4">
        <div className="grid max-h-[50vh] gap-2 overflow-y-auto">
          {groups.map((group) => {
            const unavailable = group.member_count - group.available_count
            return (
              <div key={group.id} className="flex items-center justify-between gap-3 border border-line px-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium" title={group.name}>{group.name}</span>
                    {group.id === activeGroupId ? <Badge tone="info">使用中</Badge> : null}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-mist">
                    {group.member_count} 个模型 · {group.available_count} 可用
                    {unavailable > 0 ? <span className="text-warn"> · {unavailable} 不可用</span> : null}
                  </div>
                </div>
                <Button
                  variant="line"
                  className="shrink-0 px-3"
                  disabled={!group.available_count}
                  onClick={() => onApply(group.id)}
                >
                  应用
                </Button>
              </div>
            )
          })}
          {!groups.length ? (
            <div className="px-3 py-8 text-center text-sm text-mist">还没有分组，可在下方保存当前已选，或去「管理分组」新建</div>
          ) : null}
        </div>

        <div className="border-t border-line pt-4">
          <div className="text-xs uppercase tracking-[0.16em] text-mist">把当前已选保存为新分组</div>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="分组名称"
              maxLength={128}
            />
            <Button
              className="shrink-0"
              disabled={!name.trim() || !currentTargetCount}
              onClick={() => {
                onSaveNew(name.trim())
                setName('')
              }}
            >
              保存
            </Button>
          </div>
          <div className="mt-2 text-[11px] text-mist">当前已选 {currentTargetCount} 个目标</div>
        </div>

        <div className="flex items-center justify-between">
          <button type="button" className="text-xs text-signal hover:underline" onClick={onManage}>
            管理分组
          </button>
          <Button variant="line" onClick={onClose}>关闭</Button>
        </div>
      </div>
    </Dialog>
  )
}
