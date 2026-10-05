# Requirements Document

## Introduction

资讯收集当前只做"采集 + 展示"：Telegram 公开频道的内容全部入库，管理端按时间/渠道/类型浏览，没有内容质量判断，广告与低价值内容会混入瀑布流。

本需求在既有采集链路上增加一个 AI 判定后处理环节：内容先全部入库，再由异步 worker 用管理员指定的上游账号与模型做两件事——识别并过滤广告、对有用资讯价值打分。判定为广告的条目被标记并默认隐藏（保留数据、可恢复查看），高价值条目获得分数与"精选"标记，精选支持筛选。模型支持图片输入时，判定会把条目的封面/图片一并送入。

## Glossary

- **资讯条目**：`info_items` 表中的一条采集内容，含文本、媒体、来源等字段。
- **AI 判定**：对资讯条目执行的一次结构化调用，产出广告与否、价值分数、分类与理由。
- **广告条目**：AI 判定 `is_ad = true` 的资讯条目。
- **价值分数**：AI 判定给出的 0-100 整数，越高表示对用户价值越大。
- **精选**：价值分数达到阈值或人工标记的高价值标记 `is_featured`。
- **判定配置**：账号、模型、阈值、是否传图等一组全局设置。
- **判定状态**：条目所处阶段，取 `pending` / `processing` / `done` / `failed` / `skipped`。
- **入队**：条目入库时被置为待判定状态，等待 worker 处理。

## Requirements

### Requirement 1: 入库与判定解耦

**User Story:** AS 管理员, I want 采集内容先全部落库再异步判定, so that 采集链路不被 AI 调用阻塞且不丢内容。

#### Acceptance Criteria

1. WHEN 采集器写入一条新的资讯条目, THE System SHALL persist the item with 判定状态 `pending` and commit it before any AI call.
2. WHILE 判定配置未启用, THE System SHALL keep collected items at 判定状态 `pending` without performing AI calls.
3. IF 采集写入成功而判定调用失败, THEN THE System SHALL keep the item persisted with 判定状态 `failed` and an error message.
4. The System SHALL process 判定 in a background worker that reads pending items outside the采集 transaction.
5. WHEN 管理员手动触发重新判定, THE System SHALL reset the selected items to 判定状态 `pending` and let the worker reprocess them.

### Requirement 2: 判定配置

**User Story:** AS 管理员, I want 自由选择上游账号与模型并配置阈值, so that 判定使用我信任的模型且成本可控。

#### Acceptance Criteria

1. The System SHALL persist a global 判定配置 containing `enabled`、`account_id`、`model`、图片张数上限、精选分数阈值与是否隐藏广告。
2. WHEN 管理员保存判定配置, THE System SHALL validate that the referenced upstream account exists.
3. IF 判定配置启用但未选择上游账号或模型, THEN THE System SHALL reject the save with HTTP 400.
4. The System SHALL accept an empty模型 value and resolve the account's first available model at呼叫时间.
5. The System SHALL expose the判定配置 through an authenticated admin endpoint with resolved account name for display.
6. The System SHALL treat the精选分数阈值的合法范围 as 0 through 100.

### Requirement 3: 广告识别与过滤

**User Story:** AS 管理员, I want 自动识别并过滤广告, so that 瀑布流不被营销内容占据。

#### Acceptance Criteria

1. WHEN worker 判定一条资讯为广告, THE System SHALL set该条目 `ai_label` to `ad`.
2. WHILE 判定配置开启隐藏广告, THE System SHALL set广告条目 `is_hidden` to true.
3. The System SHALL keep广告条目的正文与媒体数据 in storage after隐藏.
4. WHEN 管理员请求包含隐藏条目的列表, THE System SHALL return广告条目 with its `ai_label` and 判定原因.
5. WHEN 管理员取消某条目的隐藏, THE System SHALL restore该条目 to the default列表 without changing its AI label.
6. IF 判定结果无法解析为约定的结构化字段, THEN THE System SHALL mark该条目 判定状态 `failed` and leave `is_hidden` unchanged.

### Requirement 4: 价值打分与精选

**User Story:** AS 管理员, I want 有用的资讯获得分数与精选标记, so that 我能快速找到高价值内容。

#### Acceptance Criteria

1. WHEN worker 完成一条资讯的判定, THE System SHALL persist an integer 价值分数 in the range 0 through 100.
2. WHEN一条非广告条目的价值分数达到精选分数阈值, THE System SHALL set `is_featured` to true.
3. The System SHALL persist a short判定理由 and a分类标识 for每条已判定条目.
4. WHEN 管理员手动设置某条目的精选状态, THE System SHALL persist the manual value and the item SHALL keep that value on subsequent判定 unless the item is判定 again.
5. WHILE 一条条目被判定为广告, THE System SHALL keep `is_featured` false.

### Requirement 5: 判定输入与多模态

**User Story:** AS 管理员, I want 判定按模型能力决定是否传图, so that 支持视觉的模型能更准确判断，纯文本模型也可用。

#### Acceptance Criteria

1. The System SHALL always send the条目文本、来源名称与链接 in the判定请求.
2. WHILE 判定模型的有效输入能力包含图片, THE System SHALL attach up to图片张数上限 ready 状态的条目媒体 as image parts.
3. WHILE 判定模型的有效输入能力不包含图片, THE System SHALL send text-only input.
4. IF 条目没有 ready 状态的媒体, THEN THE System SHALL send text-only input regardless of模型能力.
5. The System SHALL not transmit media bytes larger than the configured per-image byte limit.

### Requirement 6: 筛选与展示

**User Story:** AS 管理员, I want 按精选与分数筛选并看到判定结果, so that 我能消费整理后的内容。

#### Acceptance Criteria

1. WHEN 管理员筛选精选, THE System SHALL return only条目 with `is_featured` true.
2. WHEN 管理员按最低分数筛选, THE System SHALL return only条目 whose 价值分数 is greater than or equal to the given value.
3. WHEN 管理员按 AI 标签筛选, THE System SHALL return only条目 matching the given label.
4. The System SHALL display 价值分数、精选标记与判定状态 on资讯卡片或详情.
5. The System SHALL exclude广告条目 from the default列表 while 判定配置开启隐藏广告.
6. The System SHALL display a判定状态提示 WHEN an条目 is `pending` or `failed` and the判定配置 is enabled.

### Requirement 7: 异步处理健壮性

**User Story:** AS 管理员, I want 判定进程稳定可恢复, so that 上游抖动不会卡死队列。

#### Acceptance Criteria

1. WHEN worker 启动, THE System SHALL recover条目 stuck in 判定状态 `processing` back to `pending`.
2. IF 一条判定连续失败达到重试上限, THEN THE System SHALL leave该条目 `failed` and stop retrying until手动重置.
3. The System SHALL process pending items with a bounded per-round batch and a bounded concurrency.
4. The System SHALL record每次判定的模型标识与时间 for审计.
5. WHILE 判定配置被禁用, THE System SHALL stop making上游调用 without deleting已有判定结果.

### Requirement 8: 安全

**User Story:** AS 管理员, I want 判定沿用既有凭据与鉴权, so that 上游凭据不外泄。

#### Acceptance Criteria

1. The System SHALL make判定调用 through the existing upstream credential mechanism without exposing上游 API Key.
2. The System SHALL require administrator authentication on判定配置、重新判定与条目状态变更接口.
3. The System SHALL not write the原始上游响应或凭据 into资讯条目字段.
