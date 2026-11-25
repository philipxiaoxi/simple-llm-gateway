import { Worker } from './external-mcp.mjs'

const worker = new Worker(process.env.MCP_PLATFORM_URL, process.env.MCP_REGISTRATION_TOKEN, {
  description: 'Node.js 外部 MCP 回表示例',
})
worker.tool({
  name: 'echo', description: '原样返回输入文字',
  input_schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false },
  example: { text: 'hello' }, side_effects: false,
}, async ({ text }) => ({ text }))

const stop = new AbortController()
process.once('SIGINT', () => stop.abort())
process.once('SIGTERM', () => stop.abort())
await worker.run({ signal: stop.signal })
