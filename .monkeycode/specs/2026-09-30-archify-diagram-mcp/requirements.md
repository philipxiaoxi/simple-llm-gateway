# Requirements Document

## Introduction

本需求在现有能力平面（Capability Plane）上新增 `diagram` 能力：已授权 MCP Key 的 AI 客户端提交 Archify 的类型化 JSON 源（架构图、流程图、时序图、数据流图、状态图），平台调用内置的 Archify 渲染器生成**单文件、自包含的交互式 HTML**，并通过现有站点托管平面（`site` 能力）对外发布。发布结果与站点部署一致：具备版本历史、回滚、元信息编辑与令牌访问控制。

需求范围聚焦「AI 申请创建图表 HTML 并在站点对外展示」，复用既有的 MCP Key、能力白名单、调用日志、站点存储与访问门控，不新建独立的托管与鉴权体系。

## Glossary

- **Platform**：本 AI 一体化服务平台后端（FastAPI 同进程能力平面）。
- **Archify**：第三方 JSON-IR 图表渲染器（MIT），把类型化 JSON 渲染为单文件交互式 HTML。
- **Diagram Source**：Archify 的类型化 JSON 源，`type` 取值为 `architecture` / `workflow` / `sequence` / `dataflow` / `lifecycle`。
- **Diagram Artifact**：由 Diagram Source 渲染得到的单文件自包含 HTML，等价于一个站点版本的入口文件。
- **Capability**：能力平面中可插拔的应用单元，标识 `diagram`。
- **Capability Whitelist**：`mcp_key_capabilities` 中为某个 MCP Key 授权的 capability 集合。
- **MCP Key**：`mcp_keys` 表中前缀为 `mcp-` 的独立密钥，与聊天 `sk-` 分离。
- **Site**：`sites` 表中的静态托管单元，具备版本、访问模式与归属。
- **Site Version**：`site_versions` 表中的不可变文件快照。
- **Preview URL**：`{APP_BASE_URL}/sites/{slug}/` 形式的访问地址。
- **Renderer**：被 Platform 调用的 Archify 命令行渲染程序（Node.js）。
- **Quality**：Archify 校验档位，取值为 `showcase` / `standard`。

## Requirements

### Requirement 1: 提供 diagram 能力

**User Story:** AS 平台管理员，I want 在能力平面注册 `diagram` 能力，so that AI 客户端可以在授权后申请生成图表。

#### Acceptance Criteria

1. The Platform SHALL 在能力注册中心注册标识为 `diagram`、类别为 `diagram`、状态为 `enabled` 的能力。
2. The Platform SHALL 在服务目录接口 `/api/admin/mcp/catalog` 中返回 `diagram` 能力的名称、描述、工具清单、输入 schema 与接入元数据。
3. The Platform SHALL 在 MCP `/mcp` 与 REST `/v1/diagrams` 两个协议面暴露相同的 `diagram` 业务操作。
4. The Platform SHALL 仅向 Capability Whitelist 中包含 `diagram` 的 MCP Key 展开 `diagram_*` 工具并接受其调用。

### Requirement 2: 由 AI 提交图表源创建图表

**User Story:** AS 已授权的 AI 客户端，I want 提交类型化 JSON 源并指定图表类型，so that 平台渲染并发布图表。

#### Acceptance Criteria

1. WHEN 已授权 MCP Key 调用 `diagram_create` 并传入合法 `type` 与 `source`，THE Platform SHALL 渲染 Diagram Artifact 并发布为一个 Site 版本。
2. The Platform SHALL 接受 `type` 取值为 `architecture`、`workflow`、`sequence`、`dataflow`、`lifecycle`，并拒绝其它取值。
3. The Platform SHALL 接受 `source` 为 JSON 对象或 JSON 字符串，并在渲染前校验其可解析。
4. WHEN 调用未提供 `slug`，THE Platform SHALL 依据 `name` 或 `type` 生成唯一 slug 并新建 Site。
5. WHEN 调用提供了既有且归属该 MCP Key 的 `slug`，THE Platform SHALL 为该 Site 追加一个新的 Site Version。
6. WHEN 调用提供了已被其它 MCP Key 占用的 `slug`，THE Platform SHALL 返回 409 且不创建任何记录。
7. The Platform SHALL 在创建响应中返回 `site`、`version` 与 `preview_url`。

### Requirement 3: 渲染校验与可修复诊断

**User Story:** AS AI 客户端，I want 在源不合法时拿到结构化诊断，so that 我可以修正源并重试。

#### Acceptance Criteria

1. WHEN Diagram Source 未通过 Renderer 校验，THE Platform SHALL 返回 `invalid_request` 类型的错误并附 Renderer 输出的规则码与支持修复项。
2. IF Diagram Source 未通过校验，THEN THE Platform SHALL 不创建 Site Version 文件、不切换 Site 当前版本。
3. The Platform SHALL 限制 Diagram Source 大小不超过 `diagram_max_source_bytes`，超限时返回 400。
4. The Platform SHALL 校验 `quality` 取值属于 `showcase` / `standard`，缺省使用 `showcase`。

### Requirement 4: 生成自包含 HTML 并对外托管

**User Story:** AS 站点访问者，I want 通过 Preview URL 打开交互式图表，so that 我可以浏览图表。

#### Acceptance Criteria

1. The Platform SHALL 将 Diagram Artifact 作为 Site Version 的入口文件 `index.html` 落盘。
2. The Platform SHALL 通过既有托管路由 `GET /sites/{slug}/` 返回该 Diagram Artifact。
3. The Platform SHALL 保证 Diagram Artifact 不依赖外部网络资源即可呈现图表内容。
4. The Platform SHALL 复用既有站点的内容类型推断、SPA 兜底与缓存策略，不新增托管路由。

### Requirement 5: 版本历史与回滚

**User Story:** AS 已授权 MCP Key，I want 保留历次图表版本并可回滚，so that 我可以恢复历史图表。

#### Acceptance Criteria

1. WHEN 同一 Site 再次创建图表，THE Platform SHALL 生成递增且站内唯一的 `version_no`。
2. The Platform SHALL 保留 Site Version 对应的 Diagram Source，供后续读取与再渲染。
3. WHEN 已授权 MCP Key 调用 `diagram_rollback` 并传入 `slug` 与 `version_no`，THE Platform SHALL 把 Site 当前版本切换为该 `ready` 版本。
4. IF 目标版本不属于该 Site 或不为 `ready`，THEN THE Platform SHALL 拒绝回滚并返回错误。

### Requirement 6: 编辑图表元信息与源

**User Story:** AS 已授权 MCP Key，I want 编辑图表名称、描述、slug、入口与访问模式，并读取旧版源，so that 我可以持续迭代图表。

#### Acceptance Criteria

1. WHEN 已授权 MCP Key 调用 `diagram_update`，THE Platform SHALL 仅更新请求体中出现的可编辑字段并保持其它字段不变。
2. The Platform SHALL 支持编辑 `name`、`description`、`new_slug`、`entry_file`、`spa_fallback`、`status`、`access_mode`。
3. WHEN 已授权 MCP Key 调用 `diagram_get_source`，THE Platform SHALL 返回指定版本保存的 Diagram Source 与 `type`。
4. WHEN `diagram_get_source` 未指定版本，THE Platform SHALL 返回当前版本对应的 Diagram Source。

### Requirement 7: 令牌访问控制

**User Story:** AS 已授权 MCP Key，I want 为图表设置公开或令牌保护，so that 我可以控制谁可以查看图表。

#### Acceptance Criteria

1. WHEN 已授权 MCP Key 调用 `diagram_access` 并传入 `mode=token`，THE Platform SHALL 生成或复用访问令牌并返回明文令牌与已拼接 `?token=` 的访问 URL。
2. WHEN 已授权 MCP Key 调用 `diagram_access` 并传入 `mode=public`，THE Platform SHALL 移除令牌校验并返回公开访问 URL。
3. WHEN 请求 `reset_token=true`，THE Platform SHALL 重置令牌并使旧令牌会话失效。
4. WHEN 已授权 MCP Key 调用 `diagram_access_info`，THE Platform SHALL 返回当前访问模式、令牌（令牌模式）与可直接访问的 URL。
5. The Platform SHALL 复用既有站点令牌的哈希校验、加密存储与 HMAC Cookie 会话机制。

### Requirement 8: 归属隔离与授权一致

**User Story:** AS 平台管理员，I want 图表按 MCP Key 隔离且遵循能力白名单，so that 各 Key 之间互不干扰。

#### Acceptance Criteria

1. The Platform SHALL 限定 `diagram_*` 操作只能访问归属当前 MCP Key 的图表。
2. IF 目标图表归属其它 MCP Key，THEN THE Platform SHALL 返回 404 且不泄露其存在性。
3. The Platform SHALL 在 `diagram_list` 中排除由上传归档创建的普通 Site，反之普通 `site_list` SHALL 排除由图表创建的 Site。
4. The Platform SHALL 在 `tools/list` 与 `tools/call` 两处一致地依据 Capability Whitelist 校验 `diagram` 授权。

### Requirement 9: 列表、查询与删除

**User Story:** AS 已授权 MCP Key，I want 列举、查看与删除图表，so that 我可以管理图表资产。

#### Acceptance Criteria

1. WHEN 已授权 MCP Key 调用 `diagram_list`，THE Platform SHALL 返回该 Key 创建的图表列表。
2. WHEN 已授权 MCP Key 调用 `diagram_status`，THE Platform SHALL 返回图表详情与版本列表。
3. WHEN 已授权 MCP Key 调用 `diagram_delete`，THE Platform SHALL 删除该图表及其全部版本文件与记录。

### Requirement 10: 管理端可视化

**User Story:** AS 平台管理员，I want 在管理后台管理图表，so that 我可以查看、编辑、回滚与设置令牌。

#### Acceptance Criteria

1. The Platform SHALL 在「MCP 广场」服务目录中展示 `diagram` 能力卡片并跳转到管理页 `/mcp-plaza/diagrams`。
2. The Platform SHALL 在管理页提供图表列表、预览地址、访问模式、状态与当前版本信息。
3. The Platform SHALL 在详情页提供版本列表、回滚、源查看、元信息编辑、令牌生成/重置与删除。
4. The Platform SHALL 在接入说明页列出 `diagram` 能力的 MCP 工具与 REST 端点。

### Requirement 11: 渲染器资源与沙箱约束

**User Story:** AS 平台管理员，I want 渲染过程受限且不污染环境，so that 平台稳定与安全。

#### Acceptance Criteria

1. The Platform SHALL 在受限工作目录中调用 Renderer，且不向 Renderer 传入宿主仓库根路径。
2. The Platform SHALL 为单次渲染设置超时 `diagram_render_timeout_seconds`，并在超时时终止进程并返回错误。
3. The Platform SHALL 限制并发渲染数不超过 `diagram_render_concurrency`。
4. The Platform SHALL 禁用 Renderer 的联网更新检查。
5. IF Renderer 可执行文件不可用，THEN THE Platform SHALL 返回 `renderer_unavailable` 错误且不创建 Site Version。
6. The Platform SHALL 保留 Archify 的 MIT 许可与第三方声明文件。

### Requirement 12: 限制、保留与可观测

**User Story:** AS 平台管理员，I want 复用站点保留策略与调用日志，so that 资源可控且行为可审计。

#### Acceptance Criteria

1. The Platform SHALL 复用站点版本保留与清理策略，且不清理 Site 的当前版本。
2. The Platform SHALL 为每次 `diagram_*` 调用写入 `mcp_call_logs`，记录能力、操作、耗时与错误信息。
3. The Platform SHALL 复用站点部署的重启恢复逻辑，把进程重启时残留的中间态版本标记为失败。
