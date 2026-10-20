# 安全中心（Security Center）需求文档

Feature Name: 2026-10-20-security-center
Updated: 2026-10-20

## Introduction

为管理后台新增「安全中心」，集中记录与展示三类安全信息：（1）管理员登录成功/失败等关键认证事件；（2）前端埋点上报的关键行为与运行环境信息；（3）非前端正常发起的请求（直接调试、非浏览器 User-Agent、扫描探测等），对其进行风险标记并留痕。参考 Next Terminal 的登录日志与异常访问审计思路，为自建 AI 网关提供可观测的安全审计面。

本需求的边界：安全中心是**审计与标记**能力，不做自动封禁与主动防御。网关代理接口（`/v1/*`、`/chat/*`、`/anthropic/*`、`/mcp`）由 SDK 与命令行客户端正常调用，其非浏览器 UA 属于预期行为，不纳入「异常请求」判定范围。

## Glossary

- **Security_Event**：一条安全事件记录，包含类型、级别、来源、网络与客户端上下文、风险标签与明细。
- **Admin_Console**：管理后台前端，路由前缀非 API 的 SPA 页面。
- **Console_Route**：管理端与公开 Web 接口路径集合，包含 `/api/admin/*`、`/api/public/*` 及 SPA 页面路径。
- **Proxy_Route**：面向大模型调用的网关路径集合，包含 `/v1/*`、`/anthropic/*`、`/chat/*`、`/responses/*`、`/models`、`/mcp`。
- **Frontend_Marker**：由 Admin_Console 前端在每次接口请求中携带的客户端标识请求头 `X-GW-Client: web`。
- **Beacon**：前端在关键行为发生时或页面卸载时向安全中心上报的事件。
- **Risk_Tag**：对请求附加的机器可读风险标签，例如 `non_browser_ua`、`missing_marker`、`origin_mismatch`、`scanner_ua`、`probe_path`、`automation`、`auth_fail_burst`、`unfamiliar_location`、`local_network`、`location_unknown`。
- **Direct_Request**：命中 Console_Route 且未携带合法 Frontend_Marker、或携带非浏览器 UA 的请求，即用户所述的「直接发起调试」。
- **标记记录**：安全中心仅对事件做持久化留痕与风险标签，不改变请求的响应结果，也不执行限流、封禁或对外告警。
- **Attempted_Password**：登录失败或登录锁定时客户端提交的密码原文，入库前加密、限长，仅通过单事件详情接口向已认证管理员展示。
- **Automation**：由 headless/自动化 UA 令牌、Beacon 的 `webdriver`、零屏幕尺寸或缺失 `Accept-Language` 等信号判定出的自动化客户端。
- **Trusted_Location**：管理员配置的常用登录地点白名单，粒度为国家、省或省市二级；事件归属地命中即视为可信。
- **Location_Match**：将事件归属地与国家/省/市令牌归一化后，与 Trusted_Location 条目按同粒度或更粗粒度进行匹配的过程。
- **Dedupe_Window**：同一事件指纹在时间窗口内只落一条记录的节流周期。
- **Severity**：事件级别，取值 `info`、`warn`、`critical`。

## Requirements

### Requirement 1

**User Story:** AS 平台管理员, I want 统一的 Security_Event 记录能力, so that 所有安全相关事件都有可查询、可筛选、可保留的持久化留痕。

#### Acceptance Criteria

1. The system SHALL persist each Security_Event with event type, severity, source, actor, client IP, IP location, user agent, client name, client version, redacted request headers, HTTP method, path, status code, outcome, risk tags, and detail payload.
2. The system SHALL classify each Security_Event outcome as `success` (2xx/3xx), `rejected` (401/403/429), `failed` (other 4xx/5xx), or `unknown` (no status).
3. The system SHALL redact sensitive request headers such as Authorization and Cookie before storing them.
2. The system SHALL assign each Security_Event a UTC creation timestamp and an auto-increment identifier.
3. WHEN a Security_Event carries one or more Risk_Tag values, the system SHALL store the full risk tag set with the event.
4. The system SHALL index Security_Events by creation timestamp, event type, and client IP to support filtered listing.
5. The system SHALL classify each Security_Event source as one of `web`, `api`, or `direct`.

### Requirement 2

**User Story:** AS 平台管理员, I want 记录管理员认证事件, so that 我能审计登录成功、登录失败与登录锁定。

#### Acceptance Criteria

1. WHEN an administrator submits correct credentials, the system SHALL persist a `login_success` Security_Event with the administrator username, client IP, IP location, user agent, and client name.
2. WHEN an administrator submits incorrect credentials, the system SHALL persist a `login_failure` Security_Event with the attempted username, the attempted password, client IP, IP location, user agent, and client name.
3. The system SHALL store the attempted password of a failed authentication in encrypted form, and SHALL continue to record the successful authentication event as a username-only record.
4. The system SHALL cap the stored attempted password length at the configured maximum before encryption.
5. The system SHALL return the attempted password only through the single-event detail endpoint protected by administrator authentication.
6. WHEN the login gate rejects a login attempt due to excessive failures, the system SHALL persist a `login_locked` Security_Event with severity `warn`, the attempted username, and the attempted password.
7. WHEN an administrator changes the administrator username or password, the system SHALL persist an `admin_credential_change` Security_Event with severity `warn`.
8. WHEN the Admin_Console reports a logout, the system SHALL persist a `logout` Security_Event.

### Requirement 3

**User Story:** AS 平台管理员, I want 识别并标记非前端正常发起的请求, so that 直接调试与扫描探测行为可以被发现和追溯。

#### Acceptance Criteria

1. WHEN a request targets a Console_Route, the system SHALL classify the request source as `web` if the request carries a valid Frontend_Marker and its User-Agent matches a browser pattern.
2. WHEN a request targets a Console_Route and carries a User-Agent that does not match a browser pattern, the system SHALL attach the `non_browser_ua` Risk_Tag and record a `suspicious_request` Security_Event.
3. WHEN a request targets a Console_Route without a Frontend_Marker, the system SHALL attach the `missing_marker` Risk_Tag.
4. WHEN a request targets a Console_Route without a Frontend_Marker and carries an Origin header that mismatches the request host or the configured application origin, the system SHALL attach the `origin_mismatch` Risk_Tag.
5. WHEN a request User-Agent matches a known scanner or command-line client pattern, the system SHALL attach the `scanner_ua` Risk_Tag and set event severity to `critical`.
6. WHEN a request targets a non-existent Console_Route path and returns HTTP 404, the system SHALL attach the `probe_path` Risk_Tag.
7. WHEN a request or Beacon exhibits an automation signal, the system SHALL attach the `automation` Risk_Tag and record the matched signal names in the event detail.
8. The system SHALL treat the following as automation signals: a browser-like User-Agent that matches a headless or automation token, a Beacon reporting `webdriver` true, a Beacon reporting zero screen dimensions, and a browser-like User-Agent lacking an `Accept-Language` header.
9. WHEN a single client IP produces authentication failures exceeding the configured threshold within the configured window, the system SHALL persist an `auth_fail_burst` Security_Event with severity `critical`.
10. The system SHALL exclude Proxy_Routes from `non_browser_ua` and `missing_marker` classification.
11. WHILE security audit is enabled, the system SHALL evaluate every Console_Route request for risk tags.

### Requirement 4

**User Story:** AS 平台管理员, I want 前端埋点上报关键信息, so that 客户端行为与运行环境可用于安全关联分析。

#### Acceptance Criteria

1. WHEN the Admin_Console completes authentication, the system SHALL accept a Beacon containing the page path, client time, timezone, language, screen dimensions, device pixel ratio, platform, and `webdriver` flag.
2. WHEN an administrator performs a state-changing console action, the Admin_Console SHALL emit a Beacon containing the action type and target identifier.
3. WHEN the Admin_Console page is being unloaded, the Admin_Console SHALL flush pending Beacons using a keepalive-capable transport.
4. The system SHALL authenticate every Beacon as an administrator request and SHALL reject unauthenticated Beacons.
5. The system SHALL store Beacon contents as a Security_Event of type `frontend_beacon` with severity `info`.
6. The system SHALL limit the number of Beacons accepted from a single administrator within a fixed interval.

### Requirement 5

**User Story:** AS 平台管理员, I want 一个安全中心页面, so that 我能集中查看、筛选和定位安全事件。

#### Acceptance Criteria

1. The system SHALL provide an Admin_Console page at route `/security`.
2. The Security Center page SHALL display an overview containing the login success count, login failure count, suspicious request count, and distinct source IP count for a selected time range.
3. The Security Center page SHALL provide a login log view listing authentication Security_Events ordered by creation time descending.
4. The Security Center page SHALL provide a suspicious request view listing `suspicious_request` and `auth_fail_burst` Security_Events with their Risk_Tags and severity.
5. The Security Center page SHALL provide a frontend beacon view listing `frontend_beacon` Security_Events.
6. WHEN an administrator selects a Security_Event, the page SHALL display the event detail including the full detail payload.
7. The Security Center page SHALL support filtering by time range, event type, severity, source, client IP, and a keyword query.
8. The Security Center page SHALL render responsively for both desktop and mobile layouts.

### Requirement 6

**User Story:** AS 平台管理员, I want 安全事件按保留期自动清理, so that 审计数据不会无限增长。

#### Acceptance Criteria

1. The system SHALL delete Security_Events older than the configured retention period on a daily cadence.
2. The system SHALL record the count of removed Security_Events in the cleanup execution log.

### Requirement 7

**User Story:** AS 平台管理员, I want 安全中心仅对已认证管理员开放, so that 审计数据不会泄露给未授权访问者。

#### Acceptance Criteria

1. The system SHALL require administrator authentication for every Security Center API endpoint.
2. The system SHALL require administrator authentication for the Security Center console page.
3. The system SHALL redact credentials and tokens from every stored Security_Event detail payload.
4. The system SHALL expose the stored attempted password of a failed authentication only to an authenticated administrator and only through the single-event detail response.

### Requirement 8

**User Story:** AS 平台管理员, I want 配置常用登录地点并对异地来源打标, so that 非常用地区的认证与访问能够被识别。

#### Acceptance Criteria

1. The system SHALL provide an administrator-configurable Trusted_Location list stored in the database and editable from the Security Center page.
2. The system SHALL represent each Trusted_Location entry as one of three granularities: country, province, or province-plus-city.
3. WHEN an event carries a public IP location that matches a Trusted_Location entry at the same or a broader granularity, the system SHALL consider the location trusted and attach no location risk tag.
4. WHEN an event carries a public IP location that matches no Trusted_Location entry, the system SHALL attach the `unfamiliar_location` Risk_Tag with severity `warn`.
5. WHEN an event carries a private or loopback IP address, the system SHALL attach the `local_network` Risk_Tag with severity `info` and SHALL skip Trusted_Location matching.
6. WHEN an event carries a public IP whose location lookup fails or returns no city, the system SHALL attach the `location_unknown` Risk_Tag with severity `info` and SHALL skip Trusted_Location matching.
7. The system SHALL normalize location matching on country, province, and city tokens, and SHALL tolerate the presence or absence of administrative suffixes such as 省 and 市.
8. The system SHALL treat a municipality such as 北京市 as matching both its province and city tokens.
9. The system SHALL provide an administrator-configurable IP or CIDR allowlist that bypasses Trusted_Location matching for matching source addresses.
10. WHEN a Trusted_Location entry list is empty, the system SHALL treat every public IP location as untrusted and attach the `unfamiliar_location` Risk_Tag.
11. The system SHALL apply Trusted_Location matching to `login_success`, `login_failure`, `login_locked`, `suspicious_request`, and `auth_fail_burst` events.
12. The system SHALL expose Trusted_Location entries and their enable state through the Security Center settings and matching API.
