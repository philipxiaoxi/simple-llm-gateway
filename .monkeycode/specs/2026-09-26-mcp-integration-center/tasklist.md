# 需求实施计划：MCP 接入中心

依据：`requirements.md`、`design.md`（2026-09-26-mcp-integration-center）

实现说明：接入中心直接重写原 `McpDocs.tsx`（保留路由与导出名），不新增页面文件，避免遗留死代码。

- [x] 1. 能力接入元数据（后端）
  - [x] 1.1 `CapabilitySpec` 增加 `integration: dict` 字段
  - [x] 1.2 `registry.catalog_payload()` 输出 `integration`
  - [x] 1.3 `knowledge`/`docparse`/`site` 三个 Provider 填充 `rest_endpoints` 与 `notes`
  - [x] 1.4 测试：目录接口每个内置能力的 `integration.rest_endpoints` 非空（`test_mcp_integration.py`）

- [x] 2. 接入信息接口（后端）
  - [x] 2.1 `GET /api/admin/mcp/keys/{key_id}/integration`：origin、mcp_url、rest_base_url、key 元数据、带 `authorized` 的能力列表
  - [x] 2.2 `origin` 取 `app_base_url`，为空回退请求 base_url；不返回明文密钥
  - [x] 2.3 测试：`authorized` 与白名单一致、origin 正确、响应无明文密钥

- [x] 3. 前端 API 与通用选择弹窗
  - [x] 3.1 `api.ts` 增加 `McpKeyIntegration` 类型与 `mcpKeyIntegration(keyId)`
  - [x] 3.2 新增 `ItemPickDialog`（文案参数化），`ModelPickDialog` 改为其包装层
  - [x] 3.3 `npx tsc -b` 通过

- [x] 4. 提示词生成器
  - [x] 4.1 `lib/mcpIntegration.ts`：`buildMcpPrompt`
  - [x] 4.2 同文件：`buildRestPrompt`
  - [x] 4.3 同文件：`buildMcpClientConfig`
  - [x] 4.4 同文件：`buildCapabilityReference`；只读取所选且授权能力，明文写入密钥

- [x] 5. 接入中心页面
  - [x] 5.1 重写 `pages/McpDocs.tsx`：Key 选择器（`?key=` 预选）、能力 chips、端点与复制密钥
  - [x] 5.2 四种接入方式：MCP / Skill + REST / 客户端配置 / 快速参考
  - [x] 5.3 「复制为 AI 提示词」用 `ItemPickDialog` 多选能力，默认全选可用；复制失败文本框兜底
  - [x] 5.4 `McpKeys.tsx` 每行加「接入」按钮（路由与导出名保持不变）
  - [x] 5.5 PC 双栏、移动端上下堆叠，空状态与错误态
  - [x] 5.6 `npx tsc -b` 与针对性 `eslint` 通过

- [x] 6. 文档与回归
  - [x] 6.1 README 更新接入中心与 `site_access`
  - [x] 6.2 旧静态接入说明内容已由新页面替换
  - [x] 6.3 后端 `test_mcp_integration.py` 3 项通过；接口 smoke 验证 origin/authorized/无明文
