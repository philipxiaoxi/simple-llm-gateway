import { Card } from '../components/ui'

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="max-w-full min-w-0 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-ink p-3 font-mono text-xs leading-5 text-paper [overflow-wrap:anywhere]">
      {children}
    </pre>
  )
}

export function McpDocsPage() {
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://你的站'

  return (
    <div className="grid min-w-0 max-w-full gap-4 overflow-x-hidden">
      <Card className="min-w-0 max-w-full space-y-3 overflow-hidden p-4 sm:p-5">
        <h3 className="font-medium">REST（通用）</h3>
        <CodeBlock>{`# 列出当前 Key 已授权能力
curl -s ${origin}/v1/capabilities \\
  -H "Authorization: Bearer mcp-你的密钥"

# 通用调用：任意能力的 operation
curl -s ${origin}/v1/capabilities/{capability_id}/{operation} \\
  -H "Authorization: Bearer mcp-你的密钥" \\
  -H "Content-Type: application/json" \\
  -d '{...}'

# 知识库示例
curl -s ${origin}/v1/capabilities/knowledge/search \\
  -H "Authorization: Bearer mcp-你的密钥" \\
  -H "Content-Type: application/json" \\
  -d '{"kb_id":"<kb-id>","query":"关键词","mode":"hybrid","top_k":5}'
`}</CodeBlock>
      </Card>
      <Card className="min-w-0 max-w-full space-y-3 overflow-hidden p-4 sm:p-5">
        <h3 className="font-medium">MCP（Streamable HTTP）</h3>
        <p className="text-xs text-mist">工具由注册表动态挂载；tools/list 仅返回当前 Key 白名单内能力。</p>
        <CodeBlock>{`URL:  ${origin}/mcp
Header: Authorization: Bearer mcp-你的密钥

# 知识库示例（需授权 knowledge）
#   knowledge_list
#   knowledge_search  (kb_id, query, mode?, top_k?)
`}</CodeBlock>
        <p className="break-words text-sm text-mist [overflow-wrap:anywhere]">
          新应用：后端实现 Provider 并 register → 自动进服务目录、Key 勾选、REST 通用入口与 MCP tools。聊天 sk-
          Key 不能用于本接口。
        </p>
      </Card>
    </div>
  )
}

export default McpDocsPage
