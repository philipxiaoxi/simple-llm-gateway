# AI 助手需求文档

## Introduction

在管理后台右下角提供一个可连续对话的站内 AI 助手。助手按当前登录管理员的身份搜索、描述并调用站点后台接口；只读接口直接执行，写操作先进入审批；前端流式展示思考过程、工具调用和最终回复，并支持打开对应页面。

## Glossary

- **助手**：管理后台右下角的 AI 对话面板。
- **接口目录**：由 FastAPI OpenAPI 自动扫描生成的管理接口索引。
- **只读接口**：HTTP 方法为 GET、HEAD、OPTIONS 的接口。
- **写接口**：HTTP 方法为 POST、PUT、PATCH、DELETE 的接口。
- **审批**：写接口执行前由管理员确认的记录。
- **管理员令牌**：当前浏览器登录管理后台使用的 JWT。

## Requirements

### Requirement 1 助手入口

**User Story:** AS 管理员, I want 在不离开当前页面的情况下打开助手, so that 我可以随时查询数据或执行操作。

#### Acceptance Criteria

1. WHILE 管理员已登录且拥有 `assistant:view` 权限，THE 助手 SHALL 在管理后台右下角显示悬浮入口。
2. WHEN 管理员点击悬浮入口，THE 助手 SHALL 在桌面端显示浮动面板，在移动端显示全屏面板。
3. WHILE 助手正在流式生成，THE 悬浮入口 SHALL 显示进行中状态。

### Requirement 2 模型配置

**User Story:** AS 管理员, I want 选择对话模型, so that 助手可以使用我已有的上游账号。

#### Acceptance Criteria

1. WHEN 管理员拥有 `assistant:manage` 权限，THE 助手设置 SHALL 允许选择上游账号和模型。
2. THE 助手设置 SHALL 支持温度、最大输出 tokens、工具轮次、历史消息数和系统提示词。
3. WHEN 上游账号凭据不可用，THE 助手 SHALL 提示尚未配置可用模型。

### Requirement 3 按需发现接口

**User Story:** AS 管理员, I want 助手按需查找接口, so that 上下文保持精简且能覆盖新接口。

#### Acceptance Criteria

1. WHEN 助手需要调用站点接口，THE 助手 SHALL 先调用 `api_search` 获取候选接口。
2. THE 接口目录 SHALL 由 FastAPI OpenAPI 自动生成，新增管理接口无需额外注册能力。
3. WHEN 助手需要参数详情，THE 助手 SHALL 调用 `api_describe` 读取参数、请求体 schema 和示例。
4. THE 接口目录 SHALL 按当前管理员权限过滤搜索结果。
5. THE 页面目录 SHALL 按当前管理员权限过滤，助手只看到可访问的页面。

### Requirement 4 以当前账号执行只读接口

**User Story:** AS 管理员, I want 助手使用我的身份查询数据, so that 结果与我的权限一致。

#### Acceptance Criteria

1. WHEN 助手调用只读接口，THE 后端 SHALL 使用当前请求携带的管理员令牌通过内部 ASGI 调用该接口。
2. WHEN 只读接口返回成功，THE 助手 SHALL 将结果流式展示并写入会话历史。
3. WHEN 只读接口返回 4xx 或 5xx，THE 助手 SHALL 将错误信息作为工具结果返回给模型。

### Requirement 5 写操作审批

**User Story:** AS 管理员, I want 写操作先经过我的确认, so that 助手不会在未授权时修改站点。

#### Acceptance Criteria

1. WHEN 助手调用 POST、PUT、PATCH 或 DELETE 接口，THE 后端 SHALL 创建待审批记录并暂停本轮对话。
2. WHILE 审批处于待处理状态，THE 前端 SHALL 显示方法、路径、原因和请求体，并提供批准与拒绝按钮。
3. WHILE 审批处于待处理状态，THE 后端 SHALL 拒绝同一会话的新消息。
4. WHEN 管理员批准审批，THE 后端 SHALL 使用当前管理员令牌执行接口，并继续对话。
5. WHEN 管理员拒绝审批，THE 后端 SHALL 记录拒绝结果，并允许模型给出替代方案。
6. WHEN 页面重新加载，THE 助手 SHALL 从会话历史恢复待审批记录。

### Requirement 6 页面跳转

**User Story:** AS 管理员, I want 助手直接打开对应页面, so that 我不用自己找入口。

#### Acceptance Criteria

1. WHEN 用户要求打开站内页面，THE 助手 SHALL 调用 `ui_navigate` 并传入站内路径。
2. WHEN 前端收到跳转事件，THE 前端 SHALL 使用路由跳转到目标页面并关闭助手面板。
3. IF 目标路径不属于管理后台或当前账号没有页面权限，THEN THE 后端 SHALL 拒绝跳转并返回当前账号可用的页面列表。
4. WHEN 助手找不到接口或页面，THE 助手 SHALL 直接说明无法做到，并给出可用替代。

### Requirement 7 流式展示

**User Story:** AS 管理员, I want 看到思考过程和工具调用, so that 我能判断助手是否在正确执行。

#### Acceptance Criteria

1. WHILE 模型输出思考内容，THE 前端 SHALL 在回复上方展示可折叠的思考过程。
2. WHEN 助手调用工具，THE 前端 SHALL 展示工具名称、参数、状态、耗时和结果。
3. WHEN 助手输出最终回复，THE 前端 SHALL 按事件顺序把文本和工具卡片渲染在同一条消息中。
4. WHEN 用户滚动到消息底部附近，THE 前端 SHALL 自动跟随最新内容。

### Requirement 8 连续对话

**User Story:** AS 管理员, I want 保留历史对话, so that 我可以继续之前的任务。

#### Acceptance Criteria

1. THE 助手 SHALL 按管理员隔离会话，其他管理员无法读取会话内容。
2. WHEN 管理员重新打开助手，THE 助手 SHALL 恢复最近一次会话。
3. THE 助手 SHALL 支持新建、查看历史和删除自己的会话。

### Requirement 9 安全与审计

**User Story:** AS 平台管理员, I want 助手遵守现有权限与密钥边界, so that 站点数据不会被越权访问。

#### Acceptance Criteria

1. WHEN 助手调用接口，THE 后端 SHALL 复用现有权限守卫和接口鉴权。
2. THE 接口目录 SHALL 排除助手自身接口、登录接口、密钥明文接口、导出接口和访问令牌接口。
3. THE 助手 SHALL 对工具结果中的密钥、密码和令牌字段做脱敏。
4. THE 助手 SHALL 把工具返回内容视为数据，并在系统提示词中禁止执行其中的指令。
