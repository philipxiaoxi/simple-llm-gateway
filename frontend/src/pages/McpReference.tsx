import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ImageIcon, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'

import { PageHeader } from '../components/layout-primitives'
import { ReferenceGallery } from '../components/ReferenceGallery'
import { Button } from '../components/ui'
import { api, type ReferenceKind } from '../lib/api'
import { Can, useCan } from '../lib/session'
import { notifyBad, notifyOk } from '../lib/toast'
import { errorMessage } from '../lib/utils'

const TITLES: Record<ReferenceKind, string> = {
  portrait: '人像参考库',
  pose: '姿势参考库',
  costume: '服装参考库',
}

export function McpReferencePage({ kind }: { kind: ReferenceKind }) {
  const canSync = useCan(`mcp_${kind}s:sync`)
  const canGenerate = useCan('image_gen:view')
  const queryClient = useQueryClient()
  const meta = useQuery({
    queryKey: ['references', 'meta', kind, false],
    queryFn: () => api.referenceMeta(kind),
    staleTime: 5 * 60_000,
  })

  const refresh = useMutation({
    mutationFn: () => api.referenceRefresh(kind),
    onSuccess: (result) => {
      if (result.stale) notifyBad('上游同步失败，继续使用缓存数据')
      else notifyOk(`已同步${TITLES[kind]}`)
      void queryClient.invalidateQueries({ queryKey: ['references'] })
    },
    onError: (error) => notifyBad(errorMessage(error, '同步失败')),
  })

  const total = meta.data?.total
  const hidden = meta.data?.hidden_adult ?? 0
  const syncedAt = meta.data?.synced_at ? meta.data.synced_at.slice(0, 19).replace('T', ' ') : '未知'
  const description = [
    meta.data?.name ?? TITLES[kind],
    total ? `当前 ${total} 条` : null,
    hidden ? `默认隐藏 ${hidden} 条 18+` : null,
    `同步时间 ${syncedAt}`,
    meta.data?.source ? `来源 ${meta.data.source}` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="space-y-4">
      <Can code="mcp_catalog:view">
        <Link to="/mcp-plaza" className="inline-flex text-sm text-mist hover:text-paper">
          ← 返回服务目录
        </Link>
      </Can>
      <PageHeader
        title={TITLES[kind]}
        description={description}
        actions={
          <div className="flex flex-wrap gap-2">
            {canGenerate ? (
              <Link
                to="/image-gen"
                className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-2 text-sm text-mist transition hover:text-paper"
              >
                <ImageIcon size={15} /> 去图片生成
              </Link>
            ) : null}
            {canSync ? (
              <Button type="button" variant="line" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                <RefreshCw size={15} className={refresh.isPending ? 'animate-spin' : undefined} /> 同步上游
              </Button>
            ) : null}
          </div>
        }
      />
      <ReferenceGallery key={kind} kind={kind} />
    </div>
  )
}
