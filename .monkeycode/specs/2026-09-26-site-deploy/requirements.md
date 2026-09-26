# Requirements Document

## Introduction

在 MCP 广场新增能力 `site`（站点部署）：把前端构建产物（`.zip` 静态资源包）上传为一个可访问的预览站点。系统为每个站点维护不可变版本历史，支持一键回滚、公开或令牌保护访问，并在管理端提供站点与版本管理。能力同时注册进 REST 与现有 MCP 端点，Agent 与业务系统可自动部署并取回预览地址。

本能力只托管静态文件，不执行服务端代码、不提供自定义域名与独立 TLS。预览地址采用路径形式 `{APP_BASE_URL}/sites/{slug}/`，与平台共用同一端口，复用现有前端 SPA 兜底之前的挂载顺序。

## Glossary

- **站点（Site）**：一个可独立访问的部署单元，由唯一 `slug` 标识，包含若干版本。
- **版本（Version）**：一次上传生成的不可变文件快照，含序号、入口文件、文件数与字节数。
- **当前版本（Current Version）**：预览地址实际提供服务的版本。
- **入口文件（Entry File）**：目录访问或 SPA 回退时返回的文件，默认 `index.html`。
- **slug**：URL 安全的站点标识，取值正则 `^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$`。
- **访问令牌（Access Token）**：令牌保护模式下访问站点所需的口令。
- **归档包（Archive）**：一次上传的 `.zip` 静态资源包。
- **预览地址（Preview URL）**：`{APP_BASE_URL}/sites/{slug}/`。
- **SPA 回退**：当请求路径无对应文件且无文件扩展名时，返回入口文件。
- **能力平面**：现有 `capabilities/` 注册、鉴权、日志体系。
- **MCP Key**：前缀 `mcp-` 的独立密钥，与聊天 `sk-` 分表。
- **能力标识（capability_id）**：注册表内的稳定标识 `site`，MCP 工具名前缀 `site_`。

## Requirements

### Requirement 1: 能力注册与授权

**User Story:** AS 平台管理员, I WANT `site` 出现在 MCP 广场并按 Key 授权, SO THAT 下游只能调用被允许的部署能力。

#### Acceptance Criteria

1. THE MCP 广场 SHALL 在服务目录展示已启用的 `site` 能力，包含名称、说明、版本和管理入口 `/mcp-plaza/sites`。
2. WHEN 管理员为 MCP Key 勾选 `site`, THE 系统 SHALL 允许该 Key 调用站点部署的 REST 与 MCP 接口。
3. IF MCP Key 未授权 `site`, THE 系统 SHALL 拒绝该 Key 的站点部署调用并返回 403。
4. IF 请求携带聊天 `sk-` 密钥访问站点部署的协议接口, THE 系统 SHALL 返回 401。
5. THE 系统 SHALL 把每次站点部署协议调用写入既有 MCP 调用日志，记录能力标识、操作名、成功或失败、耗时与错误信息。

### Requirement 2: 管理端上传部署

**User Story:** AS 管理员, I WANT 在 MCP 广场上传前端打包产物并立即拿到预览地址, SO THAT 演示与验收无需手工发文件。

#### Acceptance Criteria

1. WHEN 管理员在站点部署页面提交一个 `.zip` 归档包, THE 系统 SHALL 完成入参校验、创建站点或为已有站点创建一个版本，并立即返回版本标识与 `unpacking` 状态。
2. WHILE 版本处于 `unpacking`, THE 系统 SHALL 提供可查询的进度，至少包含阶段、百分比与阶段文案。
3. WHEN 解包与校验成功, THE 系统 SHALL 将版本置为 `ready` 并切换为当前版本，随后提供预览地址。
4. IF 归档校验或解包失败, THE 系统 SHALL 将版本置为 `failed`、保留站点原有当前版本、保存错误信息，并允许管理员重试。
5. WHEN 管理员在提交时未提供 slug, THE 系统 SHALL 依据站点名称生成唯一 slug 并保证不与既有站点冲突。
6. WHEN 管理员在站点详情对历史版本执行回滚, THE 系统 SHALL 把当前版本切换为该历史版本且不修改任何版本文件。
7. THE 站点部署管理页 SHALL 在 PC 与移动端均可完成上传、查看进度与版本、回滚、重试与删除；PC 保持信息密度，移动端避免控件挤压换行。
8. WHILE 归档正在上传, THE 站点部署管理页 SHALL 向管理员展示上传字节进度。

### Requirement 3: 版本管理

**User Story:** AS 管理员, I WANT 保留每次部署并可回滚, SO THAT 误传时可快速恢复。

#### Acceptance Criteria

1. WHEN 管理员对同一站点重复上传归档, THE 系统 SHALL 生成新的版本并保留此前所有未清理版本。
2. THE 系统 SHALL 为每个站点维护按创建顺序递增的版本序号，序号在同一站点内唯一。
3. WHEN 管理员查看站点详情, THE 系统 SHALL 按序号倒序展示版本列表，包含序号、状态、阶段、进度、文件数、字节数、入口文件、创建时间与是否为当前版本。
4. IF 版本状态不是 `ready`, THE 系统 SHALL 拒绝将其设为当前版本或作为回滚目标。
5. WHEN 管理员对状态为 `failed` 的版本执行重试, THE 系统 SHALL 重新执行该次部署且不改变其他版本。
6. WHILE 版本为站点当前版本, THE 系统 SHALL 拒绝删除该版本。
7. WHEN 管理员删除非当前版本, THE 系统 SHALL 删除该版本文件并保留站点与其他版本。
8. WHEN 站点版本数超过保留上限, THE 系统 SHALL 清理最旧的、非当前的、已就绪的版本文件并保留这些版本的元数据。

### Requirement 4: 预览访问

**User Story:** AS 使用者, I WANT 通过一个地址直接打开部署的站点, SO THAT 我可以分享演示或内部页面。

#### Acceptance Criteria

1. THE 系统 SHALL 通过 `{APP_BASE_URL}/sites/{slug}/` 提供当前版本的静态文件访问。
2. WHEN 站点访问模式为公开, THE 系统 SHALL 向任意访问者返回站点文件。
3. WHEN 站点访问模式为令牌保护且请求携带有效访问令牌, THE 系统 SHALL 返回站点文件。
4. IF 站点访问模式为令牌保护且请求未携带有效访问令牌, THE 系统 SHALL 返回 401 与令牌输入页面。
5. WHEN 访问者在令牌输入页提交正确令牌, THE 系统 SHALL 写入签名 Cookie，使后续静态资源请求免重复输入。
6. WHEN 请求路径在该版本内无对应文件、路径不含文件扩展名且站点启用 SPA 回退, THE 系统 SHALL 返回入口文件。
7. WHEN 请求路径命中实际文件, THE 系统 SHALL 按文件扩展名返回正确的 `Content-Type` 并附带 `X-Content-Type-Options: nosniff`。
8. WHEN 请求路径指向入口文件或 HTML 文件, THE 系统 SHALL 返回 `Cache-Control: no-cache`；其他文件 SHALL 返回长缓存。
9. IF 站点状态为停用, THE 系统 SHALL 返回 404。
10. IF 站点当前版本文件已被清理, THE 系统 SHALL 返回 503 并提示重新部署。
11. WHEN 托管 HTML 使用以单个 `/` 开头的 `src`、`href` 等根绝对资源路径, THE 系统 SHALL 将其改写为站点前缀，使资源来自该站点目录。
12. WHEN 托管 HTML 未声明 `<base>`, THE 系统 SHALL 注入指向所在目录的 `<base href>`，使相对资源正确解析。
13. THE 系统 SHALL 对托管 CSS 中以 `/` 开头的 `url(...)` 与 `@import` 执行同样的前缀改写。
14. THE 平台 Service Worker SHALL NOT 把 `/sites/` 下的文档导航兜底为管理端外壳。

### Requirement 5: 上传校验与限制

**User Story:** AS 平台管理员, I WANT 上传被严格校验, SO THAT 恶意或异常归档不会影响主机。

#### Acceptance Criteria

1. THE 系统 SHALL 只接受扩展名为 `.zip` 的归档包。
2. IF 归档大小超过上限, THE 系统 SHALL 拒绝并返回 400。
3. IF 解包后的文件总数或总字节数超过上限, THE 系统 SHALL 拒绝并返回 400。
4. IF 归档中包含绝对路径、`..` 路径段、反斜杠路径或符号链接条目, THE 系统 SHALL 拒绝并返回 400。
5. IF 归档中不存在入口文件且无法确定唯一候选入口, THE 系统 SHALL 拒绝并返回 400。
6. WHEN 归档的全部条目位于同一个顶层目录内, THE 系统 SHALL 以该顶层目录内容作为站点根。
7. THE 系统 SHALL 在临时目录完成解包与校验，只有全部通过后才以原子方式移动到版本目录。

### Requirement 6: 协议面部署与查询

**User Story:** AS 集成方, I WANT 用 REST 或 MCP 部署站点并拿到地址, SO THAT Agent 与业务系统可以自动发布预览。

#### Acceptance Criteria

1. WHEN 已授权 `site` 的 MCP Key 调用 `POST /v1/sites` 并提交归档, THE 系统 SHALL 返回 `unpacking` 状态的版本标识并在后台完成部署。
2. WHILE 版本处于 `unpacking`, THE 系统 SHALL 允许已授权 Key 通过版本状态接口查询阶段与进度。
3. WHEN 已授权 Key 调用站点列表接口, THE 系统 SHALL 仅返回该 Key 创建的站点。
4. WHEN 已授权 Key 对自有站点调用回滚接口, THE 系统 SHALL 切换当前版本并返回新的当前版本。
5. THE 系统 SHALL 提供 MCP tools `site_deploy`、`site_list`、`site_status`、`site_rollback`、`site_delete`、`site_access`；其中 `site_deploy` SHALL 在单次调用内完成解包并返回终态。
6. WHEN 已授权 Key 调用 `site_access` 或 `POST /v1/sites/{slug}/access` 并指定 `public` 或 `token`, THE 系统 SHALL 切换站点访问模式。
7. WHEN 已授权 Key 在令牌模式下生成或重置令牌, THE 系统 SHALL 返回一次性令牌明文，并使此前令牌立即失效。
8. IF 已授权 Key 调用 `site_deploy` 且 base64 解码后的归档超过 MCP 入参上限, THE 系统 SHALL 返回 400 并提示改用 REST multipart。
9. IF 已授权 Key 访问不属于该 Key 的站点, THE 系统 SHALL 返回 404。

### Requirement 7: 站点生命周期与访问令牌

**User Story:** AS 管理员, I WANT 停用、重置令牌与删除站点, SO THAT 我可以控制内容何时对谁可见。

#### Acceptance Criteria

1. WHEN 管理员停用站点, THE 系统 SHALL 使其预览地址返回 404 并保留版本文件。
2. WHEN 管理员重新启用站点, THE 系统 SHALL 恢复预览访问。
3. WHEN 管理员删除站点, THE 系统 SHALL 删除该站点全部版本文件与记录。
4. WHEN 管理员为令牌保护站点生成或重置访问令牌, THE 系统 SHALL 生成新令牌并使旧令牌立即失效。
5. THE 系统 SHALL 在令牌生成或重置的响应中完整展示令牌明文，此后的列表与详情 SHALL 仅展示掩码。
6. THE 系统 SHALL 保留最近 N 个版本，并在版本文件超过保留天数时清理文件、保留站点与版本元数据。
