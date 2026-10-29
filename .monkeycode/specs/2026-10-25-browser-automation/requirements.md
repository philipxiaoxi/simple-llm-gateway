# Requirements Document

## Introduction

在 MCP 广场新增能力 `browser`：由 AI 通过 MCP 工具远程控制一个运行在 Docker 内的无头浏览器，完成导航、读取页面、点击、输入等操作，并把截图以可访问的链接回传给用户查看。能力复用现有能力平面的 MCP Key 鉴权、白名单与调用日志；管理端提供在线浏览器会话页，可实时查看当前页面截图与地址。

浏览器底层采用 Playwright 控制 Chromium。能力以「会话」为隔离单位：每个 MCP Key 可创建有限数量的独立浏览器上下文，会话空闲超时或主动关闭后释放。

本需求聚焦服务端可托管的在线浏览器与截图回传；不做桌面端真实浏览器接管、不做用户本机浏览器控件。

## Glossary

- **browser 能力**：MCP 广场中注册的能力 `browser`，对外暴露浏览器控制工具与 REST 接口。
- **浏览器会话（Browser Session）**：一次隔离的浏览器上下文与页面集合，归属某个 MCP Key，带独立 Cookie/Storage。
- **会话令牌 / 截图令牌**：由 `APP_SECRET_KEY` 签名的自包含令牌，用于无需登录即可查看截图。
- **快照（Snapshot）**：页面可访问性树或精简 DOM 的文本表示，供 AI 理解页面并选择操作目标。
- **元素引用（ref）**：快照中为可交互节点生成的稳定标识，用于后续点击/输入定位。
- **私网地址**：回环、私有网段、链路本地（含云元数据 `169.254.169.254`）与保留地址。
- **后台浏览器进程**：承载 Chromium 的进程，可与网关同容器，也可独立为 sidecar 容器。

## Requirements

### Requirement 1: 能力注册与授权

**User Story:** AS 平台管理员, I want 在 MCP 广场看到并授权 `browser` 能力, so that 只有被授权的 MCP Key 才能控制浏览器。

#### Acceptance Criteria

1. WHEN 后端启动且 `BROWSER_ENABLED=true`, THE 系统 SHALL 在能力注册表中注册 `browser`，并在服务目录展示其名称、说明与 `tools` 数量。
2. WHEN 管理员创建或编辑 MCP Key, THE 系统 SHALL 允许勾选 `browser` 作为授权能力。
3. IF 未授权 `browser` 的 MCP Key 调用任一浏览器工具或接口, THEN THE 系统 SHALL 返回 `403 permission_error` 且不创建浏览器会话。
4. THE 系统 SHALL 在 `browser` 的接入说明中列出全部工具、参数、返回值与使用示例。
5. WHILE `BROWSER_ENABLED=false`, THE 系统 SHALL 不注册 `browser`，且不影响其他能力启动。

### Requirement 2: 会话生命周期

**User Story:** AS 被授权的 AI 客户端, I want 创建并复用浏览器会话, so that 多步操作在同一页面状态上连续执行。

#### Acceptance Criteria

1. WHEN 调用方执行 `browser_open` 并传入合法 URL, THE 系统 SHALL 创建一个归属该 MCP Key 的隔离浏览器上下文与页面，并返回 `session_id`、当前 URL、标题与页面快照。
2. WHILE 浏览器会话处于活跃状态, THE 系统 SHALL 在同一上下文内执行后续导航、点击、输入、截图操作。
3. IF 活跃会话数达到全局上限或该 MCP Key 的会话上限, THEN THE 系统 SHALL 返回 `429 too_many_sessions` 并提示关闭闲置会话。
4. WHEN 会话空闲超过 `BROWSER_IDLE_TTL_SECONDS`, THE 系统 SHALL 关闭浏览器上下文并将会话标记为 `expired`。
5. WHEN 调用方执行 `browser_close`, THE 系统 SHALL 关闭上下文、结束页面并将会话标记为 `closed`。
6. THE 系统 SHALL 提供 `browser_list_sessions` 返回当前 MCP Key 的会话列表（`session_id`、状态、当前 URL、最近活动时间）。

### Requirement 3: 导航与页面理解

**User Story:** AS AI 客户端, I want 打开网页并获得可读快照, so that 我能理解页面并决定下一步操作。

#### Acceptance Criteria

1. WHEN 执行导航操作, THE 系统 SHALL 仅接受 `http` 或 `https` 协议的 URL。
2. IF 目标主机解析为私网地址或位于禁止列表, THEN THE 系统 SHALL 拒绝导航并返回 `400 blocked_host`。
3. WHEN 导航完成或在超时时间内达到 `domcontentloaded`, THE 系统 SHALL 返回页面标题、最终 URL 与快照文本。
4. THE 系统 SHALL 为快照中的可交互节点生成 `ref`，并在返回文本中附带可读的元素描述。
5. WHEN 调用 `browser_snapshot`, THE 系统 SHALL 返回当前页面的最新快照，并可限制快照最大字符数。
6. IF 导航或快照超过 `BROWSER_NAV_TIMEOUT_MS`, THEN THE 系统 SHALL 返回 `504 navigation_timeout` 并保留会话可用。

### Requirement 4: 页面交互

**User Story:** AS AI 客户端, I want 点击、输入、按键与等待, so that 我能完成表单与多步流程。

#### Acceptance Criteria

1. WHEN 调用 `browser_click` 并传入 `ref` 或 `selector`, THE 系统 SHALL 对目标元素执行点击，并返回操作后的 URL 与快照。
2. WHEN 调用 `browser_type` 并传入 `ref` 或 `selector` 与文本, THE 系统 SHALL 填充目标输入框；当 `submit=true` 时 SHALL 触发提交。
3. WHEN 调用 `browser_press_key`, THE 系统 SHALL 向当前焦点发送指定按键（如 `Enter`、`Escape`）。
4. WHEN 调用 `browser_select_option`, THE 系统 SHALL 在下拉框选择指定值。
5. WHEN 调用 `browser_back` 或 `browser_forward`, THE 系统 SHALL 在会话历史中前进或后退，并返回新页面快照。
6. WHEN 调用 `browser_wait`, THE 系统 SHALL 等待指定毫秒数、元素出现或文本出现，任一条件满足即返回。
7. IF 目标元素在超时时间内不可见或不可操作, THEN THE 系统 SHALL 返回 `422 element_not_found` 并附带最新快照，保持会话可用。

### Requirement 5: 截图与回传

**User Story:** AS 用户, I want 查看 AI 操作时的页面截图, so that 我能确认浏览器实际状态。

#### Acceptance Criteria

1. WHEN 调用 `browser_screenshot`, THE 系统 SHALL 截取当前视口或整页，保存为 `png`（可选 `jpeg`）并返回 `screenshot_url`。
2. THE 系统 SHALL 让 `screenshot_url` 在无需登录的情况下可访问，并使用带过期时间的签名令牌保护。
3. WHEN 截图文件不存在或令牌无效/过期, THE 截图访问接口 SHALL 返回 `401` 或 `410`，且不泄露文件系统路径。
4. THE 系统 SHALL 在截图中限制最大字节数 `BROWSER_MAX_SCREENSHOT_BYTES`，超限时返回 `413 too_large`。
5. WHEN `include_screenshot=true` 的导航或交互操作返回结果, THE 系统 SHALL 附带本次操作后的截图链接。
6. WHEN 截图超过 `BROWSER_SCREENSHOT_TTL_SECONDS`, THE 系统 SHALL 允许访问接口返回过期错误，并由清理任务删除文件与记录。

### Requirement 6: 管理端在线浏览器页

**User Story:** AS 管理员, I want 在 MCP 广场查看在线浏览器会话与实时截图, so that 我能观察和协助 AI 的操作。

#### Acceptance Criteria

1. WHEN 管理员打开 `browser` 应用页, THE 页面 SHALL 展示当前所有浏览器会话（归属 Key、状态、当前 URL、最近活动时间）。
2. WHEN 管理员选择某个会话, THE 页面 SHALL 展示该会话的最新截图，并支持手动刷新与自动刷新。
3. WHEN 管理员执行关闭会话操作, THE 系统 SHALL 关闭对应浏览器上下文并刷新列表。
4. WHILE 会话处于 `expired` 或 `closed`, THE 页面 SHALL 以只读方式展示历史信息，不再请求实时截图。
5. WHEN 页面在移动端展示, THE 会话列表与截图 SHALL 采用单列布局且不横向溢出。

### Requirement 7: 安全与资源约束

**User Story:** AS 平台管理员, I want 限制浏览器的访问范围与资源占用, so that 该能力不会成为内网探测或资源耗尽入口。

#### Acceptance Criteria

1. THE 系统 SHALL 拒绝 `file`、`chrome`、`data`、`javascript` 等非 HTTP(S) 导航协议。
2. IF 导航主机解析为私网、回环、链路本地或保留地址, THEN THE 系统 SHALL 拒绝该次导航。
3. IF 配置了 `BROWSER_ALLOWED_HOSTS`, THEN THE 系统 SHALL 仅允许导航到列表内的主机及其子域。
4. THE 系统 SHALL 默认禁用下载与文件上传，并默认不提供任意 JavaScript 求值工具。
5. THE 系统 SHALL 为每个会话使用独立的 `BrowserContext`，会话之间不共享 Cookie 与存储。
6. THE 系统 SHALL 限制全局与会话并发、导航超时、截图大小与截图保留时长，并在超出限制时返回可读错误。
7. THE 系统 SHALL 在会话关闭、过期或删除时释放浏览器上下文与页面资源。

### Requirement 8: 调用日志与可观测性

**User Story:** AS 管理员, I want 在调用记录中看到浏览器操作详情, so that 我能审计 AI 访问了哪些站点。

#### Acceptance Criteria

1. WHEN 执行任一浏览器操作, THE 系统 SHALL 写入 MCP 调用日志，`capability_id=browser`，`operation` 为对应动作名。
2. THE 调用日志 SHALL 记录来源（MCP/REST）、客户端 IP、User-Agent、耗时、成功/失败、错误类型与 HTTP 状态。
3. THE 调用日志 SHALL 记录脱敏后的请求参数；截图内容 SHALL 以链接与字节数记录，不写入日志正文。
4. WHEN 操作因安全策略被拒绝, THE 调用日志 SHALL 记录拒绝原因，且不产生浏览器页面访问。

### Requirement 9: Docker 部署

**User Story:** AS 运维, I want 在 Docker 中启用该能力, so that 浏览器依赖可控且可独立限制资源。

#### Acceptance Criteria

1. WHERE 启用浏览器能力, THE 镜像 SHALL 包含 Chromium 运行所需的系统依赖与 Playwright 浏览器二进制。
2. WHILE 运行于容器内, THE 浏览器 SHALL 使用无头模式，并在需要时启用容器兼容启动参数。
3. THE 部署配置 SHALL 允许通过环境变量启用/禁用该能力，并在禁用时不影响网关启动。
4. THE 部署配置 SHALL 通过数据卷持久化截图目录，并为浏览器进程提供足够的共享内存。
5. WHERE 采用独立浏览器容器, THE 网关 SHALL 通过内部网络访问浏览器容器，浏览器端口不对外暴露。

### Requirement 10: 兼容与降级

**User Story:** AS 管理员, I want 在浏览器不可用时得到明确提示, so that 不影响其他能力使用。

#### Acceptance Criteria

1. IF Chromium 启动失败, THEN THE 系统 SHALL 返回 `503 browser_unavailable`，并在日志中记录启动错误。
2. IF 单次操作导致页面崩溃, THEN THE 系统 SHALL 将受影响的会话标记为 `error`，并允许调用方新建会话。
3. WHEN 能力被禁用, THE 系统 SHALL 在服务目录隐藏该能力，并让旧的调用返回 `404 capability_disabled`。
