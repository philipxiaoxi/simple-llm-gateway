# 用户与角色权限（Role & Permission）需求文档

Feature Name: 2026-10-23-role-permissions
Updated: 2026-10-23

## Introduction

为管理后台引入用户管理与角色管理，把当前「只有一个管理员账号、登录即拥有全部功能」的模型，升级为「账号 → 单个角色 → 权限点集合」的 RBAC 模型。权限粒度到页面上的具体功能（按钮级），例如查看、新增、编辑、删除、导入、导出、测试、执行、上传、下载。前端对无权限者隐藏入口与按钮，后端对每个管理接口强制校验，做到「界面看不到」与「接口调不动」一致。

升级后现有唯一账号自动归属内置角色「管理员」，其权限为全部功能且不可修改、不可删除，因此升级前后该账号的行为保持不变。

范围边界：

- 本轮只做**功能级权限**（能不能做某件事），不做数据级权限（不区分「只能操作自己创建的 Key」这类数据范围）。
- 一个账号**绑定恰好一个角色**，不做多角色叠加。
- 网关代理接口（`/v1/*`、`/anthropic/*`、`/mcp`）与公开自助页由 API Key / MCP Key 鉴权，不纳入本模型。

## Glossary

- **Admin_User**：管理后台账号，凭用户名与密码登录，绑定恰好一个 Role。
- **Role**：权限点集合，包含名称、内置标记与 Permission 列表。
- **Permission**：功能级授权最小单位，形如 `{Resource}:{Action}`，例如 `accounts:create`。
- **Resource**：权限点所属的页面或模块，与导航入口一一对应，例如 `accounts`、`logs`、`mcp_knowledge`。
- **Action**：Resource 上的具体功能，例如 `view`、`create`、`update`、`delete`、`export`、`probe`、`run`、`upload`。
- **Permission_Catalog**：全部合法 Permission 的登记表，是权限校验、角色编辑界面与校验合法性的唯一真源。
- **Builtin_Admin_Role**：内置 Role，名称为「管理员」，权限集合为通配 `*`，不可编辑、不可删除。
- **Wildcard_Grant**：Builtin_Admin_Role 持有的通配权限 `*`，命中即放行全部 Permission。
- **Route_Permission**：某个管理接口（HTTP 方法与路径模板）所需的 Permission。
- **Default_Action_Mapping**：由 HTTP 方法推导 Action 的默认规则：`GET → view`、`POST → create`、`PUT/PATCH → update`、`DELETE → delete`。
- **Permission_Guard**：后端据 Route_Permission 做校验的依赖组件。
- **Session**：管理后台登录会话，由 JWT 承载用户名与 token 版本。
- **Admin_Console**：管理后台前端应用。
- **Nav_Entry**：侧栏、移动底部 Tab 与命令面板中的页面入口。
- **Console_Page**：管理后台的一个页面或子页面。
- **Permission_Denied_Response**：权限校验失败时返回的 403 响应，含可读文案与 `permission` 字段。
- **Protected_Admin**：内置角色所属的账号，系统保留至少一个，用于避免权限模型被锁死。

## Requirements

### Requirement 1

**User Story:** AS 平台管理员, I want 账号与角色的持久化模型, so that 可以为不同使用者分配不同功能。

#### Acceptance Criteria

1. The system SHALL persist each Role with id, unique name, builtin flag, permission list, creation time, and last update time.
2. The system SHALL persist each Admin_User with username, password hash, role reference, created time, last login time, enabled flag, and a token version used for session invalidation.
3. The system SHALL bind each Admin_User to exactly one Role.
4. The system SHALL record the Permission list of a Role as Permission codes validated against the Permission_Catalog.

### Requirement 2

**User Story:** AS 平台管理员, I want 一个恒定的内置管理员角色, so that 系统始终存在一个可恢复全部权限的入口。

#### Acceptance Criteria

1. The system SHALL provide a Builtin_Admin_Role named 管理员 whose permission list is the Wildcard_Grant.
2. WHEN a request modifies or deletes the Builtin_Admin_Role, the system SHALL reject the request with a 403 Permission_Denied_Response.
3. WHEN a request assigns the Builtin_Admin_Role to an Admin_User, the system SHALL accept the request only if the requesting Admin_User holds the Wildcard_Grant.

### Requirement 3

**User Story:** AS 平台管理员, I want 角色管理界面, so that 可以按功能粒度创建与调整角色。

#### Acceptance Criteria

1. The system SHALL expose interfaces for listing, creating, updating, and deleting non-builtin Roles.
2. WHEN a request deletes a Role referenced by any Admin_User, the system SHALL reject the request with a 409 response carrying a readable reason.
3. The system SHALL reject a Role update whose permission codes fall outside the Permission_Catalog with a 400 response listing the unknown codes.
4. The system SHALL expose the Permission_Catalog grouped by Resource, including for each Action a localized label.
5. The Admin_Console SHALL render a role editor that groups permissions by Console_Page and offers per-page select-all and clear-all.

### Requirement 4

**User Story:** AS 平台管理员, I want 用户管理界面, so that 可以开通与回收账号。

#### Acceptance Criteria

1. The system SHALL expose interfaces for listing, creating, updating, deleting Admin_User records, and for resetting an Admin_User password.
2. WHEN a request deletes the last Admin_User holding the Wildcard_Grant, the system SHALL reject the request with a 409 response carrying a readable reason.
3. WHEN a request lowers the Role of the last Admin_User holding the Wildcard_Grant, the system SHALL reject the request with a 409 response carrying a readable reason.
4. WHEN an Admin_User is created or its password or role changes, the system SHALL increment that Admin_User token version to invalidate existing sessions.
5. The system SHALL reject a username that duplicates an existing Admin_User with a 409 response carrying a readable reason.

### Requirement 5

**User Story:** AS 平台管理员, I want 每个管理接口按权限强制校验, so that 界面之外的直接调用同样被拦截。

#### Acceptance Criteria

1. The system SHALL map every `/api/admin/*` interface to a Route_Permission derived from its Resource and Default_Action_Mapping, with an explicit override table for interfaces whose Action differs from the default.
2. WHEN an authenticated Admin_User requests an interface whose Route_Permission is absent from its Role, the system SHALL respond with a 403 Permission_Denied_Response including the Permission code.
3. WHILE an Admin_User holds the Wildcard_Grant, the system SHALL allow every `/api/admin/*` interface.
4. The system SHALL leave proxy interfaces, MCP interfaces, public pages, and unauthenticated interfaces outside the Permission_Guard scope.
5. WHEN a new `/api/admin/*` interface is registered without an explicit override, the system SHALL derive its Route_Permission from the Default_Action_Mapping of its Resource.

### Requirement 6

**User Story:** AS 平台管理员, I want 权限变更即时生效, so that 调整角色后无需逐个通知重新登录。

#### Acceptance Criteria

1. WHILE an Admin_User is authenticated, the system SHALL resolve its Role and Permission list from storage on each request.
2. WHEN a Role permission list changes, the system SHALL apply the new Permission list to subsequent requests of the bound Admin_User without a new login.
3. WHEN an Admin_User is disabled or its password changes, the system SHALL reject its existing Session with a 401 response.

### Requirement 7

**User Story:** AS 平台管理员, I want 图片生成与视频生成分权, so that 两类生成能力可以授予不同的人。

#### Acceptance Criteria

1. The system SHALL define distinct Resources for 图片生成 and 视频生成, each carrying the actions view, task_create, task_retry, task_delete, and settings.
2. WHEN a request creates a generation task with kind image, the system SHALL require the 图片生成 task_create Permission; WHEN the kind is video, the system SHALL require the 视频生成 task_create Permission.
3. WHEN a request reads, retries, or deletes a generation task, the system SHALL resolve the task kind and require the corresponding Permission.
4. The system SHALL filter the generation task list to the kinds permitted for the requesting Admin_User.
5. The system SHALL require a shared generation Permission for generation configuration and reference-material upload interfaces.

### Requirement 8

**User Story:** AS 平台管理员, I want 前端入口与按钮级隐藏, so that 使用者只看到自己可用的功能。

#### Acceptance Criteria

1. The Admin_Console SHALL expose a session state containing the current Admin_User username, role name, and Permission list.
2. WHEN the Admin_Console renders Nav_Entry items, the system SHALL include only entries whose page Permission is present in the Permission list.
3. WHEN the Admin_Console routes to a Console_Page without the corresponding page Permission, the system SHALL redirect to a not-authorized page.
4. WHEN a Console_Page renders an action, the system SHALL omit the action control if its Permission is absent from the Permission list.
5. The Admin_Console SHALL provide one shared mechanism for action gating such that a Console_Page declares a Permission code instead of duplicating permission logic.
6. WHEN an interface responds 403, the Admin_Console SHALL present a readable notification naming the missing Permission.

### Requirement 9

**User Story:** AS 平台管理员, I want 权限与账号变更可审计, so that 可以追溯谁改了什么。

#### Acceptance Criteria

1. WHEN an Admin_User, Role, or Role permission list changes, the system SHALL record an audit event with actor username, target, action, and timestamp.
2. The system SHALL expose the audit events of user and role changes through the existing security center event list.

### Requirement 10

**User Story:** AS 运维, I want 升级平滑, so that 现有账号在升级后保持不变。

#### Acceptance Criteria

1. WHEN the system upgrades a database whose Admin_User records carry no Role reference, the system SHALL create the Builtin_Admin_Role and bind every existing Admin_User to that Role.
2. WHILE an Admin_User carries no Role reference, the system SHALL resolve its Permission list as the Wildcard_Grant.
3. The system SHALL apply the data model change through the idempotent startup migration mechanism.

### Requirement 11

**User Story:** AS 平台管理员, I want 用户与角色页自身受控, so that 权限管理能力可以单独授予。

#### Acceptance Criteria

1. The system SHALL define a Resource for 用户管理 carrying the actions view, create, update, delete, reset_password, and toggle_status, and a Resource for 角色管理 carrying the actions view, create, update, delete, and assign_permissions.
2. WHEN an Admin_User without the Wildcard_Grant creates or updates a Role, the system SHALL reject permission codes absent from its own Permission list with a 403 Permission_Denied_Response.
3. WHEN an Admin_User without the Wildcard_Grant assigns a Role to an Admin_User, the system SHALL restrict the assignment to Roles whose permission lists are subsets of its own Permission list.
