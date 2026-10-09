# Requirements Document

## Introduction

资讯收集目前只支持服务端主动采集（Telegram / RSS / 微信公众号），缺一条「外部 Agent 主动把资讯推进来」的写入通道。本需求在 MCP 广场新增资讯上报能力：Agent 用一把 MCP Key，通过 MCP 或 REST 把一条或多条资讯（文本 + 可选媒体字节）上报进平台，落进现有资讯库并直接公开可见。

已确认的范围决策：

- **媒体用字节上送**：Agent 上传图片/视频字节，平台直存本地，不抓取 Agent 给的远程 URL（绕过 SSRF 白名单限制，交付地址稳定）。
- **归属内置「其他」渠道**：上报内容统一进 `kind=manual / identifier=other` 的现有渠道，不按 Key 分渠道。
- **上报即公开**：上报成功即标记为精选并对公开页可见。

明确不做：不做远程 URL 转存、不做按 Key 独立渠道、不做人工审核队列（以总开关 + 限流 + 手动隐藏作为护栏）。

## Glossary

- **上报服务**：能力平面上 `capability_id = info` 的能力，对外名「资讯上报」。
- **调用方**：持有一把 MCP Key、调用上报接口的 Agent。
- **MCP Key**：前缀 `mcp-`、用于能力平面鉴权的独立密钥。
- **内置「其他」渠道**：`info_sources` 中 `kind = manual`、`identifier = other` 的渠道，复用现有 `sources.ensure_manual_source`。
- **媒体项**：一条上报附带的单个图片或视频。
- **去重键**：一条上报的 `external_id`，在渠道内唯一。
- **上报总开关**：控制上报服务是否接受写入的全局配置 `info_report_enabled`。
- **MCP 工具**：通过 `/mcp` 暴露的工具 `info_report` 与 `info_report_batch`。
- **REST 端点**：通过 `/v1/info/report` 暴露的 HTTP 接口。

## Requirements

### Requirement 1: 能力注册与双协议接入

**User Story:** AS 平台维护者, I WANT 把资讯上报注册为一项能力, SO THAT Agent 能用同一套 MCP Key 通过 MCP 或 REST 两种方式上报。

#### Acceptance Criteria

1. THE 系统 SHALL 注册 `capability_id = info` 的能力并使其出现在 MCP 广场服务目录。
2. THE 系统 SHALL 通过 MCP 工具与 REST 端点两种协议暴露同一套上报功能。
3. WHEN 调用方以 MCP Key 调用上报, THE 系统 SHALL 校验该 Key 的白名单包含 `info` 能力。
4. IF 调用方未授权 `info` 能力, THEN THE 系统 SHALL 返回 403 permission_error。
5. THE 系统 SHALL 把每次上报写入 MCP 调用日志，记录 Key、能力、操作、成功与否与耗时。
6. THE 系统 SHALL 在能力目录中返回上报服务的 REST 端点与调用约束说明。

### Requirement 2: 单条资讯上报

**User Story:** AS 调用方, I WANT 上报一条资讯的文本与元数据, SO THAT 平台能收录这条内容。

#### Acceptance Criteria

1. WHEN 调用方上报一条资讯, THE 系统 SHALL 接受 `text`、`title`、`url`、`author`、`published_at`、`external_id` 字段。
2. THE 系统 SHALL 要求 `text` 与媒体项至少存在其一。
3. IF `text` 与媒体项同时缺失, THEN THE 系统 SHALL 返回 400 invalid_request。
4. WHEN 上报成功, THE 系统 SHALL 返回条目 id、状态、去重标记与媒体项数量。
5. THE 系统 SHALL 对 `text` 长度与 `title` 长度执行上限截断或拒绝。
6. WHEN 调用方提供 `published_at`, THE 系统 SHALL 按 ISO8601 解析并入库；解析失败时 THE 系统 SHALL 以采集时间入库。

### Requirement 3: 媒体字节上送

**User Story:** AS 调用方, I WANT 直接上传图片/视频字节, SO THAT 媒体无需依赖可访问的远程地址即可稳定交付。

#### Acceptance Criteria

1. WHEN 调用方通过 REST 上报媒体, THE 系统 SHALL 接受 `multipart/form-data` 的文件字段。
2. WHEN 调用方通过 MCP 上报媒体, THE 系统 SHALL 接受 `media` 数组中 base64 编码的媒体项。
3. THE 系统 SHALL 依据媒体项的 `Content-Type` 或显式 `kind` 判定其为图片或视频。
4. IF 媒体项类型不属于 `image/*` 或 `video/*`, THEN THE 系统 SHALL 拒绝该条上报并返回 unsupported_content_type。
5. THE 系统 SHALL 校验单文件字节数、单条媒体项数量与单条媒体总字节数不超过配置上限。
6. IF 任一媒体项超过上限, THEN THE 系统 SHALL 拒绝该条上报并返回 too_large。
7. WHEN 媒体校验通过, THE 系统 SHALL 把字节直接写入条目媒体目录并置媒体项状态为 ready。
8. THE 系统 SHALL 在写入媒体时探测图片宽高，并据此计算条目封面与可展示媒体数。
9. THE 系统 SHALL 对媒体上送全程不发起任何上游网络请求。

### Requirement 4: 归属与来源标识

**User Story:** AS 平台维护者, I WANT 上报内容集中归档并保留来源线索, SO THAT 我能区分上报内容与自动采集内容。

#### Acceptance Criteria

1. WHEN 上报成功, THE 系统 SHALL 把条目归档到内置「其他」渠道。
2. THE 系统 SHALL 复用已存在的内置「其他」渠道，不重复创建。
3. WHILE 调用方未提供 `author`, THE 系统 SHALL 以该 MCP Key 的名称写入条目 `author_name`。
4. WHEN 调用方提供 `author`, THE 系统 SHALL 以 `author` 写入条目 `author_name`，并截断到字段长度上限。

### Requirement 5: 去重与幂等

**User Story:** AS 调用方, I WANT 重复上报同一内容时得到幂等结果, SO THAT 重试不会产生重复条目。

#### Acceptance Criteria

1. WHEN 调用方提供 `external_id`, THE 系统 SHALL 以 `external_id` 作为去重键。
2. WHILE 调用方未提供 `external_id`, THE 系统 SHALL 以 `url` 与 `text` 的内容摘要生成去重键。
3. IF 去重键在内置「其他」渠道已存在, THEN THE 系统 SHALL 返回 duplicate = true 与已存在条目的 id。
4. THE 系统 SHALL 在并发重复上报时通过渠道内唯一约束保证只落一条。

### Requirement 6: 公开可见性

**User Story:** AS 调用方, I WANT 上报的内容立即公开可见, SO THAT 内容能立刻被浏览方看到。

#### Acceptance Criteria

1. WHEN 上报成功, THE 系统 SHALL 把条目置为精选 `is_featured = true`。
2. WHEN 上报成功, THE 系统 SHALL 把条目置为公开可见 `is_hidden = false`。
3. WHEN 上报成功, THE 系统 SHALL 标记该精选为人工设定，使 AI 判定不改写精选状态。
4. THE 系统 SHALL 使上报条目同时出现在管理端瀑布流与公开页。
5. THE 系统 SHALL 允许管理员沿用现有隐藏操作把上报条目下架。

### Requirement 7: 护栏与限额

**User Story:** AS 平台维护者, I WANT 上报能力可控可限, SO THAT 公开写入口不会被滥用或塞爆。

#### Acceptance Criteria

1. THE 系统 SHALL 提供全局配置 `info_report_enabled` 作为上报总开关。
2. IF 上报总开关关闭, THEN THE 系统 SHALL 拒绝上报并返回 503 service_disabled。
3. THE 系统 SHALL 按 MCP Key 限制单位时间内的上报次数。
4. IF 调用方超过上报频率, THEN THE 系统 SHALL 返回 429 rate_limited 并给出重试提示。
5. THE 系统 SHALL 通过配置限制单文件字节数、单条媒体项数量、单条媒体总字节数、单次批量条数与文本长度上限。

### Requirement 8: 批量上报

**User Story:** AS 调用方, I WANT 一次调用上报多条资讯, SO THAT 批量场景减少往返。

#### Acceptance Criteria

1. WHEN 调用方发起批量上报, THE 系统 SHALL 接受资讯数组并在一次响应内返回逐条结果。
2. THE 系统 SHALL 限制单次批量条目数不超过配置上限。
3. THE 系统 SHALL 对批量中的每条资讯独立执行去重与校验，单条失败不影响其余条目。
4. WHEN 批量完成, THE 系统 SHALL 返回创建数、去重数与逐条失败明细。
5. IF 批量条目数超过上限, THEN THE 系统 SHALL 返回 400 invalid_request。

### Requirement 9: 接入说明与管理可见性

**User Story:** AS 平台维护者, I WANT 接入说明自动反映上报能力, SO THAT 下游 Agent 能自助接入。

#### Acceptance Criteria

1. THE 系统 SHALL 在能力目录的 `integration` 中返回上报 REST 端点、鉴权方式与约束说明。
2. WHEN 后端注册上报能力, THE 接入中心 SHALL 自动展示该能力，无需修改接入中心页面代码。
3. THE 系统 SHALL 使 MCP 广场服务目录展示上报能力卡片并链接到资讯管理页。
