# Requirements Document

## Introduction

抖音下载与资讯收集（Telegram 采集）共用同一个 TikHub 账号，即一份 Base URL 与 API Key 可跨平台使用。当前凭据只落在抖音下载页的「TikHub 解析 API」区，资讯收集页仅展示状态并跳转回抖音页配置，造成入口割裂、后端表名与业务耦合等命名债。

本需求把 TikHub 凭据抽离为一份全局共享配置：后端新增中立的 `tikhub_settings` 存储与统一管理接口（兼容迁移旧 `douyin_settings` 数据），前端提供唯一的配置弹窗组件并从多个入口复用，使抖音页与资讯页都能读写同一份凭据。

## Glossary

- **TikHub 凭据**：访问 TikHub 托管 API 所需的 Base URL 与 API Key 组合，Key 加密存储。
- **共享存储**：全局单例的 TikHub 配置记录，仅保留一份，抖音下载与资讯收集共用。
- **统一管理接口**：管理员鉴权下读写 TikHub 凭据的 REST 接口集合。
- **配置弹窗**：前端唯一负责展示与编辑 TikHub 凭据的对话框组件。
- **入口**：能够打开配置弹窗的页面位置。
- **环境变量兜底**：数据库未提供有效凭据时，从进程环境读取的备用凭据。

## Requirements

### Requirement 1: 共享 TikHub 凭据存储

**User Story:** AS 管理员, I want 一份跨抖音下载与资讯采集共享的 TikHub 凭据, so that 仅配置一次即可在两个功能中直接使用。

#### Acceptance Criteria

1. The System SHALL persist TikHub credentials as a single global record containing a Base URL and an encrypted API Key.
2. WHEN 管理员保存 API Key, THE System SHALL encrypt the API Key with the key derived from `APP_SECRET_KEY` before persistence.
3. WHILE 读取 TikHub 凭据, THE System SHALL prefer the persisted global record over environment variables.
4. IF 持久化记录缺失或 API Key 解密失败, THE System SHALL fall back to the environment variables `TIKHUB_BASE_URL` and `TIKHUB_API_KEY`.
5. The System SHALL return the default Base URL `https://api.tikhub.io` WHEN no Base URL is configured.

### Requirement 2: 旧配置迁移

**User Story:** AS 管理员, I want 升级后原有抖音 TikHub 配置自动生效, so that 无需重新填写凭据。

#### Acceptance Criteria

1. WHEN 系统启动且共享存储中没有任何记录, THE System SHALL copy the legacy `douyin_settings` Base URL and encrypted API Key into the shared record.
2. The System SHALL treat the migration as idempotent so repeated startups produce the same single record.
3. The System SHALL leave the legacy `douyin_settings` table and its data intact for rollback.
4. IF 旧表不存在或旧记录为空, THE System SHALL start with no shared record.
5. WHILE 共享存储中存在一条已清空的记录, THE System SHALL keep the cleared state across restarts and skip the legacy migration.

### Requirement 3: 统一凭据管理接口

**User Story:** AS 管理员, I want 通过统一接口读取、保存、清除 TikHub 凭据, so that 前端任意入口共用同一套 API。

#### Acceptance Criteria

1. WHEN 管理员请求凭据状态, THE System SHALL return Base URL、`configured` 标志、`has_key` 标志、凭据来源与更新时间。
2. WHEN 管理员提交以 `http://` 或 `https://` 开头的 Base URL 与非空 API Key, THE System SHALL persist the credentials and return the updated status.
3. IF 提交的 Base URL 非空且不以 `http://` 或 `https://` 开头, THEN THE System SHALL reject the request with HTTP 400.
4. IF 管理员提交空 API Key, THEN THE System SHALL reject the request with HTTP 400.
5. WHEN 管理员清除凭据, THE System SHALL reset the shared record to an empty state so that the environment fallback takes effect and the completed legacy migration remains applied across restarts.
6. The System SHALL require administrator authentication on every TikHub credential endpoint.
7. The System SHALL omit the plaintext API Key from every response body.

### Requirement 4: 统一配置弹窗与多入口

**User Story:** AS 管理员, I want 在任意相关页面打开同一个配置弹窗, so that 无需切换到特定页面即可完成配置。

#### Acceptance Criteria

1. The System SHALL provide a single reusable TikHub configuration dialog component in the frontend.
2. WHEN 管理员在抖音下载页点击配置入口, THE System SHALL open the shared configuration dialog.
3. WHEN 管理员在资讯渠道页点击配置入口, THE System SHALL open the shared configuration dialog.
4. WHEN 保存或清除在当前弹窗内成功, THE System SHALL refresh the凭据 status for every mounted entry point.
5. WHILE API Key 已配置, THE dialog SHALL display the configured state and present the Key input empty with a configured placeholder.
6. WHILE 保存请求进行中, THE dialog SHALL disable the editable fields and the submit control.

### Requirement 5: 一致的凭据状态展示

**User Story:** AS 管理员, I want 各入口一致地展示凭据状态, so that 能明确判断当前是否已配置。

#### Acceptance Criteria

1. The System SHALL display a `configured` badge（已配置 / 未配置）on the抖音下载页.
2. The System SHALL display the凭据 source（管理页 / 环境变量）WHEN a source is available.
3. WHILE TikHub 凭据未配置, THE资讯渠道页 SHALL display a warning banner whose action opens the shared configuration dialog.
4. The System SHALL display the凭据 update time WHEN 持久化记录包含更新时间.

### Requirement 6: 旧接口兼容

**User Story:** AS 已有调用方, I want 旧抖音 provider 接口继续可用, so that 升级不破坏现有集成。

#### Acceptance Criteria

1. The System SHALL continue serving `/api/admin/mcp/douyin/provider` with the same status JSON shape.
2. WHEN 调用方通过旧接口保存或清除凭据, THE System SHALL apply the change to the共享存储.
