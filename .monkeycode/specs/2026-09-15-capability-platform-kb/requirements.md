# Requirements Document

## Introduction

在现有 AI 一体化服务平台（LLM Gateway）之上，新增**通用能力平面（Capability Platform）**：统一注册、调用、日志；对外同时提供 **REST** 与 **MCP** 双协议。管理端新增 **「MCP 广场」** 页面作为能力浏览与配置入口；鉴权使用**独立的 MCP Key**（与现有聊天网关 `api_keys` 表及 `sk-` 聊天 Key **分离**）。每把 MCP Key 可配置允许使用的 MCP 服务/能力白名单。

第一版用**知识库（纯文本上传 + 向量检索 / 全文检索）**作为探针能力，验证扩展骨架；向量 embedding 调用本站已配置的 **OpenAI 兼容 embeddings 上游**。后续可按同一契约挂接 OCR、视频理解等能力。

本需求范围：**能力平面骨架 + MCP 广场 + 独立 MCP Key + 知识库 MVP**。图片 OCR / 视频理解等列为后续能力，不在本版验收内。

## Glossary

- **系统**：本仓库 LLM Gateway（FastAPI 后端 + React 管理端 + SQLite）。
- **能力 / MCP 服务（Capability）**：可插拔业务单元，对外暴露稳定的 `capability_id`（亦称服务 id）与输入输出契约；内部可替换 Provider。在「MCP 广场」中以服务卡片展示。
- **能力平面（Capability Platform）**：能力注册、MCP Key 鉴权、按 Key 授权、调用路由、统一错误与调用日志的公共层。
- **MCP 广场**：管理端独立一级页面，用于浏览已注册能力、管理知识库内容、管理 MCP Key、查看调用说明与试检索。
- **MCP Key**：专用于 REST 能力 API 与 MCP 端点的访问凭证；与聊天网关 `ApiKey`（绑定上游聊天账号的 `sk-`）**分表存储、分套管理**，禁止混用校验逻辑直接复用聊天 Key 记录。
- **Key 能力授权**：MCP Key 与 `capability_id` 的多对多白名单；仅白名单内能力可被该 Key 调用与在 tools 列表中发现。
- **REST 能力 API**：路径前缀 `/v1/capabilities/*`（或文档固定的等价前缀），仅接受有效 MCP Key。
- **MCP 端点**：路径前缀 `/mcp`（Streamable HTTP），将已授权能力映射为 MCP Tools。
- **知识库（Knowledge Base）**：一组纯文本文档的集合，支持分块入库、向量检索与全文检索；作为第一个 `capability_id`（建议 `knowledge`）落地。
- **文档（Document）**：知识库内一条已入库文本。
- **分块（Chunk）**：检索粒度单元。
- **检索模式**：`vector`（语义向量）、`fulltext`（关键词/全文）、`hybrid`（合并；向量不可用时降级全文）。
- **Embedding 上游**：本站配置的 OpenAI 兼容 embeddings 接口（独立配置项或选定上游账号的 embeddings 端点），供向量化调用。
- **管理员**：JWT 登录管理后台的角色。
- **下游调用方**：持有 MCP Key 的客户端或 Agent。
- **Provider**：能力内部实现（Chroma、SQLite FTS、embeddings 客户端等）。

## Requirements

### Requirement 1: 能力平面骨架

**User Story:** AS 平台维护者, I want 一套可扩展的能力注册与调用骨架, so that 后续 OCR/视频等能力只需实现 Provider 即可挂上 REST 与 MCP。

#### Acceptance Criteria

1. THE 系统 SHALL 维护能力注册表，每条记录至少包含：`capability_id`、`name`、`description`、`version`、`category`（可选）、`input_schema` 摘要、`status`（`enabled` / `disabled`）。
2. WHEN 下游调用方请求能力列表接口且提供有效 MCP Key, THE 系统 SHALL 仅返回该 Key **已授权** 且全局为 `enabled` 的能力（含 `capability_id`、`name`、`description`、`version`）。
3. WHEN 下游调用方请求某一 `capability_id` 的契约说明, IF 该能力全局 `enabled` 且该 Key 已授权, THE 系统 SHALL 返回契约说明；IF 未授权, THE 系统 SHALL 返回 HTTP 403；IF 不存在或全局 `disabled`, THE 系统 SHALL 返回 HTTP 404。
4. THE 系统 SHALL 规定扩展点：实现 Provider（参数校验 + 执行入口）并注册后，自动进入 MCP 广场目录；REST 与 MCP 共用同一执行路径，鉴权与日志主路径保持不变。

### Requirement 2: 独立 MCP Key 与按服务授权

**User Story:** AS 管理员, I want 单独管理 MCP Key 并为每把 Key 勾选可用服务, so that 聊天 Key 与能力 Key 隔离、权限可裁剪。

#### Acceptance Criteria

1. THE 系统 SHALL 使用独立数据表存储 MCP Key（建议表名 `mcp_keys`），字段至少包含：`id`、`name`、`key_prefix`、`key_hash`、可逆密文或等价安全存储、`status`（`active` / `disabled`）、`created_at`、`last_used_at`（可选）。
2. THE 系统 SHALL 禁止将现有聊天 `api_keys` 记录直接当作 MCP Key 校验通过；聊天 Key 请求 `/v1/capabilities/**` 或 `/mcp` 时按无效凭证处理（HTTP 401）。
3. WHEN 管理员创建 MCP Key, THE 系统 SHALL 生成随机明文密钥（建议前缀 `mcp-` 以与 `sk-` 区分）、仅在创建响应中完整展示一次，并要求选择至少一个初始授权 `capability_id`（或允许先建空白名单但调用一律 403，二选一在设计中写死；**推荐创建时至少选一个**）。
4. THE 系统 SHALL 提供 MCP Key 与 `capability_id` 的授权关联表（建议 `mcp_key_capabilities`），支持事后增删授权。
5. WHEN 下游使用 MCP Key 调用某能力, IF 该 Key 状态非 `active`, THE 系统 SHALL 返回 401；IF 未授权该 `capability_id`, THE 系统 SHALL 返回 403。
6. WHEN 下游访问 MCP tools/list, THE 系统 SHALL 仅列出该 Key 已授权且全局 `enabled` 的工具。
7. WHILE 管理员已登录, THE 系统 SHALL 在 MCP 广场内提供 MCP Key 的列表、创建、停用/启用、删除、编辑授权服务；删除 Key 时级联删除其授权关系。
8. WHEN 管理员复制或查看调用示例, THE 系统 SHALL 使用 MCP Key 示例，禁止引导用户把聊天 `sk-` 填进能力 API。

### Requirement 3: 统一鉴权头与调用日志

**User Story:** AS 管理员, I want 能力调用可审计, so that 能排查滥用与故障。

#### Acceptance Criteria

1. WHEN 下游调用方访问 `/v1/capabilities/**` 或 `/mcp`, THE 系统 SHALL 接受 `Authorization: Bearer <mcp-key>` 或 `x-api-key: <mcp-key>`，并走 MCP Key 解析（哈希查找），与聊天 Key 解析函数分离。
2. IF 凭证缺失、无法解析或不存在, THE 系统 SHALL 返回 HTTP 401。
3. WHEN 一次能力调用完成（成功或失败）, THE 系统 SHALL 写入调用记录，至少包含：时间、MCP Key id/前缀/名称、`capability_id`、操作名（如 `search`）、耗时毫秒、成功/失败、错误摘要。
4. WHILE 管理员已登录, WHEN 在 MCP 广场打开调用记录, THE 系统 SHALL 按时间倒序展示近期记录（分页或至少最近 100 条）。

### Requirement 4: REST 协议面

**User Story:** AS 应用开发者, I want 用标准 HTTP JSON 调用已授权能力, so that 任意语言可接入。

#### Acceptance Criteria

1. THE 系统 SHALL 提供 REST 前缀 `/v1/capabilities`，JSON 为主；文件上传使用 `multipart/form-data`。
2. WHEN 鉴权与授权通过, THE 系统 SHALL 在可配置超时内（默认 60s）返回结果或超时错误。
3. THE 系统 SHALL 使用统一错误体：`{"error": {"type": "<string>", "message": "<string>", "code": "<optional>"}}`，并映射 HTTP 400/401/403/404/409/413/500/503。
4. WHEN 路径不存在, THE 系统 SHALL 返回 404。

### Requirement 5: MCP 协议面

**User Story:** AS Agent 用户, I want 通过 MCP 发现并调用已授权服务, so that Cursor / Claude 等可直接用。

#### Acceptance Criteria

1. THE 系统 SHALL 在同一 FastAPI 进程挂载 MCP Streamable HTTP 端点（建议 `/mcp`），使用官方 **MCP Python SDK**（`mcp` 包）。
2. WHEN MCP 客户端握手且 MCP Key 有效, THE 系统 SHALL 仅暴露该 Key 白名单内的 tools。
3. WHEN MCP 客户端调用工具, THE 系统 SHALL 执行与对应 REST 相同的业务逻辑，并返回可读文本（及可选结构化载荷）。
4. IF MCP Key 无效, THE 系统 SHALL 拒绝连接或拒绝调用；禁止匿名写操作与匿名检索。
5. MVP 知识库相关 MCP 工具为只读：`knowledge_list`、`knowledge_search`；上传与删除仅管理员接口提供。

### Requirement 6: MCP 广场（管理端）

**User Story:** AS 管理员, I want 在名为「MCP 广场」的页面管理服务与 Key, so that 能力运营入口清晰。

#### Acceptance Criteria

1. WHILE 管理员已登录, THE 系统 SHALL 在主导航提供「MCP 广场」入口，路由建议 `/mcp-plaza`（最终路径在设计中固定），文案为「MCP 广场」。
2. WHEN 管理员打开 MCP 广场, THE 系统 SHALL 以卡片或列表展示已注册能力（名称、描述、状态、分类），知识库服务可见。
3. THE 系统 SHALL 在 MCP 广场内提供分区或子路由，至少覆盖：服务目录、知识库管理、MCP Key 管理、调用记录、接入说明（REST curl + MCP 连接示例）。
4. THE 系统 SHALL 遵循项目既有响应式约定：PC 与移动端均可完成主流程。
5. WHEN 全局将某能力设为 `disabled`, THE 系统 SHALL 拒绝所有 Key 对该能力的调用，即使白名单仍包含该 id。

### Requirement 7: 知识库管理（管理员）

**User Story:** AS 管理员, I want 创建知识库并上传纯文本, so that 有可检索的内容。

#### Acceptance Criteria

1. WHILE 管理员已登录并位于 MCP 广场的知识库分区, THE 系统 SHALL 支持知识库列表、创建、重命名、删除。
2. WHEN 管理员创建知识库, THE 系统 SHALL 要求非空 `name`（1–128 字符），可选 `description`（≤2000 字符），并生成稳定 `kb_id`。
3. WHEN 管理员上传纯文本, THE 系统 SHALL 接受 `.txt` / `.md` 的 multipart 文件或 JSON `text`；单次正文最大 2 MiB（可配置）。
4. IF 内容为空或超过上限, THE 系统 SHALL 返回 400 或 413，且不修改已有文档。
5. WHEN 文档入库成功, THE 系统 SHALL 分块（默认约 500–800 字符、重叠约 80–120 字符，可配置），写入 `document_id`、`kb_id`、`chunk_index`、`source_name`，并对各 Chunk 调用 Embedding 上游写入向量库。
6. IF 入库时 Embedding 上游失败, THE 系统 SHALL 仍保存文档与全文索引，将向量状态标记为 `pending` 或 `failed`，并在管理端可见；允许后续重试向量化（MVP 可提供「重新向量化」按钮或下次检索前补偿，设计中写死一种）。
7. WHEN 管理员删除文档或知识库, THE 系统 SHALL 级联删除 Chunk、FTS 条目与向量集合数据。
8. THE 系统 SHALL 展示每个知识库的文档数、Chunk 数、向量就绪状态、最近更新时间。

### Requirement 8: 知识库检索（REST）

**User Story:** AS 下游调用方, I want 在授权后检索知识库, so that Agent 能注入上下文。

#### Acceptance Criteria

1. WHEN 下游 `POST` 知识库检索端点（路径在设计中固定，建议 `/v1/capabilities/knowledge/search`）提交 `kb_id`、`query`、可选 `mode`（`vector` | `fulltext` | `hybrid`，默认 `hybrid`）、可选 `top_k`（默认 5，最大 20）, 且 MCP Key 已授权 knowledge 能力, THE 系统 SHALL 返回 `hits`：每项含 `chunk_id`、`document_id`、`text`、`score`、`source_name`。
2. WHEN `mode` 为 `vector`, THE 系统 SHALL 做向量检索；IF Embedding 上游不可用或该库无可用向量, THE 系统 SHALL 返回 HTTP 503 或 400，错误信息明确说明向量不可用（禁止静默改走全文）。
3. WHEN `mode` 为 `fulltext`, THE 系统 SHALL 仅用全文检索，不依赖 Embedding 上游。
4. WHEN `mode` 为 `hybrid`, THE 系统 SHALL 合并向量与全文结果并去重排序；IF 向量侧不可用, THE 系统 SHALL **自动降级为全文**，并在响应中带 `degraded: true` 与 `degraded_reason`（或等价字段），HTTP 200。
5. IF `kb_id` 不存在, THE 系统 SHALL 返回 404；IF `query` 空白, THE 系统 SHALL 返回 400；IF 库空, THE 系统 SHALL 返回 200 与空 `hits`。
6. IF MCP Key 未授权 knowledge, THE 系统 SHALL 返回 403。

### Requirement 9: 知识库 MCP 工具

**User Story:** AS Agent, I want 用 MCP 工具列举与检索知识库, so that 对话中直接用资料。

#### Acceptance Criteria

1. THE 系统 SHALL 在 knowledge 能力下注册工具：`knowledge_list`、`knowledge_search`（名称固定写入接入说明）。
2. WHEN Agent 调用上述工具且 Key 已授权, THE 系统 SHALL 行为与 REST 对等（含 hybrid 降级语义）。
3. MVP 阶段 MCP 工具保持只读。

### Requirement 10: Embedding 与开源选型

**User Story:** AS 平台维护者, I want 用热门开源组件并复用本站 embeddings 上游, so that 少造轮子。

#### Acceptance Criteria

1. THE 系统 SHALL 使用 **ChromaDB**（`chromadb`）`PersistentClient`，数据目录可配置（默认 `data/chroma`）。
2. THE 系统 SHALL 通过 **OpenAI 兼容 embeddings HTTP API** 生成向量（配置项至少含：base URL、API Key、model 名；可指向本网关将来暴露的 embeddings 或直连已有上游）。MVP 允许在 `.env` / 设置中配置 `MCP_EMBEDDING_BASE_URL`、`MCP_EMBEDDING_API_KEY`、`MCP_EMBEDDING_MODEL`。
3. WHEN 执行 pytest, THE 系统 SHALL 支持注入 fake embedding 客户端，使 CI **不依赖外网**。
4. THE 系统 SHALL 使用官方 **MCP Python SDK**（`mcp`）。
5. THE 系统 SHALL 用 SQLAlchemy + SQLite 存知识库元数据；全文检索优先 **SQLite FTS5**。
6. THE 系统 SHALL 将依赖写入 `backend/requirements.txt`，设计文档注明版本与许可偏好（MIT/Apache）。

### Requirement 11: 管理端知识库试检索与说明

**User Story:** AS 管理员, I want 网页试检索并复制接入说明, so that 无需 curl 即可验收。

#### Acceptance Criteria

1. WHEN 管理员在知识库详情做试检索, THE 系统 SHALL 支持三种 `mode` 并展示 hits 与（若有）降级提示。
2. THE 系统 SHALL 展示 REST 与 MCP 接入示例，示例中的 Key 占位为 MCP Key。
3. UI 须同时适配 PC 与移动端信息架构。

### Requirement 12: 非目标（本版）

**User Story:** AS 产品负责人, I want 边界清晰, so that 范围可控。

#### Acceptance Criteria

1. THE 本版非目标包括：图片 OCR、视频理解、PDF/Office 解析、聊天 Key 与 MCP Key 合并、多管理员 RBAC、下游计费账单、外部爬虫同步、多副本 Chroma 集群、按知识库再做 ACL（本版授权粒度停在 capability 级）。
2. THE 本版与现有网关一致，默认单机部署；多进程下 Chroma/SQLite 限制须在设计文档写明。

### Requirement 13: 可测试性

**User Story:** AS 开发者, I want 自动化测试覆盖主路径, so that 可回归。

#### Acceptance Criteria

1. THE 系统 SHALL 提供 pytest：MCP Key 与聊天 Key 隔离、授权白名单、能力列表过滤、知识库 CRUD、分块、fulltext、vector 失败语义、hybrid 降级、fake embedding 入库检索。
2. WHEN 在 `backend` 执行既有测试命令, THE 新增用例 SHALL 默认不访问公网。
3. THE 系统 SHALL 对「聊天 sk- 调 /mcp 或 /v1/capabilities」有断言：期望 401。
