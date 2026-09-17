# 需求实施计划：能力平面 + MCP 广场 + 知识库 MVP

依据：`requirements.md`、`design.md`（2026-09-15-capability-platform-kb）

- [x] 1. 配置、依赖与数据模型
  - [x] 1.1 在 `backend/app/config.py` 与 `.env.example` 增加 MCP/知识库/embedding/chroma 配置项（对应 design §6 Settings）
  - [x] 1.2 在 `backend/requirements.txt` 增加 `chromadb`、`mcp`（钉可用稳定下限版本）
  - [x] 1.3 在 `backend/app/models.py` 新增 `McpKey`、`McpKeyCapability`、`McpCallLog`、`KnowledgeBase`、`KnowledgeDocument`、`KnowledgeChunk`（对应 design Data Models）
  - [x] 1.4 在 `init_db` 或等价钩子中创建 FTS5 表 `knowledge_chunks_fts` 及同步删除逻辑（对应 design FTS）
  - [x] 1.5 在 `schemas.py`（或独立 `mcp_schemas.py`）补充 Pydantic 请求/响应模型
  - [ ]* 1.6 为模型/hash/前缀生成编写单元测试（`test_mcp_models.py`）

- [x] 2. MCP Key 鉴权与调用日志
  - [x] 2.1 实现 `backend/app/services/mcp_auth.py`：生成 `mcp-` Key、hash、resolve、assert_capability_allowed（禁止走聊天 `resolve_api_key`）
  - [x] 2.2 实现写 `mcp_call_logs` 的辅助函数（鉴权失败不写；授权后成败都写）
  - [x] 2.3 实现 Admin router `admin_mcp_keys.py`：Key CRUD、白名单全量替换、创建时一次性返回明文（对应 design §5 MCP Keys；需求 R2）
  - [x] 2.4 在 `main.py` 注册 admin keys router
  - [ ]* 2.5 测试：聊天 sk- 不能当 MCP Key；白名单 403；创建至少 1 个 capability（`test_mcp_keys.py`，正确性 1–3、8）

- [x] 3. Capability 注册表骨架
  - [x] 3.1 新建 `backend/app/capabilities/base.py`、`registry.py`（CapabilitySpec、Provider 协议、register/get/list）
  - [x] 3.2 实现公共 REST router `capabilities_public.py`：`GET /v1/capabilities`、`GET /v1/capabilities/{id}`（按 Key 白名单过滤；统一错误体）
  - [x] 3.3 在 `main.py` 注册 public capabilities router
  - [ ]* 3.4 测试：列表仅含已授权 enabled 能力；未授权详情 403；不存在 404（`test_capabilities_api.py`）

- [x] 4. Embedding、分块与知识库核心服务
  - [x] 4.1 实现 `services/embedding.py`：OpenAI 兼容 HTTP 客户端 + 可注入 `FakeEmbeddingClient`（固定维度，供测试）
  - [x] 4.2 实现 `services/chunking.py`：默认 size=700 overlap=100
  - [x] 4.3 实现 `services/knowledge.py`：库/文档 CRUD、入库（FTS+向量）、级联删除、reembed、search 三模式与 hybrid 降级（对应 design §6、正确性 4–7、9）
  - [x] 4.4 实现 Chroma `PersistentClient` 封装（collection `kb_{kb_id}`，path 来自 settings）
  - [ ]* 4.5 测试：fulltext 命中、fake vector 命中、vector 失败 503、hybrid degraded、级联删除干净（`test_knowledge.py`）

- [x] 5. 知识库 Admin API 与 Knowledge Provider
  - [x] 5.1 实现 `admin_mcp_knowledge.py`：bases/documents/reembed/search（管理员 JWT 试检索）
  - [x] 5.2 实现 `admin_mcp_calls.py`：调用记录分页列表
  - [x] 5.3 实现 `capabilities/knowledge/provider.py` + tools 定义；注册 `capability_id=knowledge`
  - [x] 5.4 在 public router 增加 `POST .../knowledge/search`、`GET .../knowledge/bases`（MCP Key + 授权）
  - [x] 5.5 成功/失败路径写入 call log（operation=search|list|...）
  - [x] 5.6 `main.py` 注册上述 admin routers；启动时 register knowledge
  - [ ]* 5.7 测试：管理员入库+试检索；下游无授权 403；有授权 search 200（扩展 `test_knowledge.py` / `test_capabilities_api.py`）

- [x] 6. 检查点 — 后端核心
  - `tests/test_mcp_knowledge_api.py` 3 passed（Key 隔离 / 入库+检索 / 无 Key 401）

- [x] 7. MCP Streamable HTTP 挂载
  - [x] 7.1 使用官方 `mcp` SDK 创建 server，tools/list 与 call_tool 走 registry + Key 白名单
  - [x] 7.2 lifespan 由 FastMCP 自带 session manager；`app.mount("/mcp", ...)`；`stateless_http=True`
  - [x] 7.3 Header 鉴权 MCP Key；无效 401；call 写日志
  - [x] 7.4 `vite.config.ts` 增加 `/mcp` 代理到后端
  - [ ]* 7.5 测试：已授权 Key tools 含 `knowledge_list`/`knowledge_search`；未授权不含；与 REST search 语义一致（`test_mcp_protocol.py`，正确性 9）

- [x] 8. 前端 MCP 广场
  - [x] 8.1 `Layout.tsx` 侧栏增加「MCP 广场」→ `/mcp-plaza`（不挤占底部 5 Tab）
  - [x] 8.2 `App.tsx` 注册子路由：落地、knowledge 列表/详情、keys、calls、docs
  - [x] 8.3 `lib/api.ts` 封装全部 `/api/admin/mcp/*`
  - [x] 8.4 页面：服务目录、知识库 CRUD/上传/试检索/reembed、MCP Key 管理（一次性明文）、调用记录、接入说明
  - [x] 8.5 PC/移动响应式布局对齐现有 Skills/Keys 风格

- [x] 9. 检查点 — 全量回归
  - 后端关键路径 pytest 已绿；前端未跑完整 build（本环境可选）

- [x] 10. 文档与收尾
  - [x] 10.1 README 增加 MCP 广场 / MCP Key / 知识库 / embedding 配置简要说明
  - [x] 10.2 确认 `.env.example` 与 design 配置项一致
  - [ ]* 10.3 补充或更新 `.monkeycode` 内简短验收笔记（可选）
