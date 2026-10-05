# Requirements Document

## Introduction

资讯收集当前支持 Telegram 公开频道。本需求新增**微信公众号**信源，复用现有 TikHub 托管 API（同一账号与 Key），走已有的适配器/采集/AI 判定链路，前端在「添加渠道」中可选渠道类型并按公众号维度订阅其历史与新发文章。

上游（TikHub `wechat_mp` v2）已验证接口：
`fetch_account_profile`、`fetch_account_articles`（单页列表 + `next_offset` 游标 + `is_end`）、`fetch_article_detail`。

## Glossary

- **微信公众号渠道**：以公众号 `username`（`gh_…` 或自定义微信号）为标识的一个采集渠道，`kind = wechat`。
- **文章标识**：公众号文章在库内的稳定唯一标识，用于 `(source_id, external_id)` 去重。
- **列表页**：`fetch_account_articles` 返回的一页文章（含标题/摘要/封面/链接/发布时间）。
- **全文模式**：列表之外再调用 `fetch_article_detail` 拉取正文与正文图片。
- **摘要模式**：只落列表页字段，正文点击跳转原文。
- **游标**：`fetch_account_articles` 的 `offset`/`next_offset`，不透明的 base64 字符串。

## Requirements

### Requirement 1: 微信公众号适配器

**User Story:** AS 管理员, I want 添加微信公众号作为采集渠道, so that 我能像 Telegram 一样订阅公众号内容。

#### Acceptance Criteria

1. The System SHALL register a source adapter with `kind = wechat` that reuses the shared TikHub base URL and API Key.
2. WHEN 管理员提交公众号 `username`（`gh_…` 或自定义微信号）或一篇 `https://mp.weixin.qq.com/s/…` 文章链接, THE System SHALL normalize it to the公众号 `username`.
3. WHEN 管理员提交文章链接, THE System SHALL resolve the公众号 `username` through `fetch_article_detail` before创建渠道.
4. The System SHALL return a渠道预览 containing nickname 与原文数量 WHEN 上游提供.
5. IF TikHub 未配置 API Key, THEN THE System SHALL reject the operation with an explicit provider-unavailable error.

### Requirement 2: 文章列表采集与分页

**User Story:** AS 管理员, I want 采集公众号历史与新发文章, so that 内容持续进入资讯流。

#### Acceptance Criteria

1. WHEN 采集一页文章列表, THE System SHALL map每条文章 to 标题、摘要、封面、原文链接、发布时间与稳定文章标识.
2. The System SHALL map上游的 opaque 翻页游标 to the渠道游标字段 without interpreting its内容.
3. WHILE 首次采集, THE System SHALL page向更老 through `next_offset` until达到回填上限或 `is_end`.
4. WHEN 增量采集, THE System SHALL fetch最新一页并以 `(source_id, external_id)` 去重，重复文章记为跳过.
5. IF 同一公众号文章在两次采集间重复出现, THEN THE System SHALL keep exactly one条目.

### Requirement 3: 采集状态与游标泛化

**User Story:** AS 管理员, I want 字符串游标渠道也能正确增量, so that 微信公众号不会每轮重复回填。

#### Acceptance Criteria

1. The System SHALL support 适配器游标为整数或字符串.
2. The System SHALL treat首次采集 as「该渠道尚无成功采集记录」，与游标类型无关.
3. WHILE 渠道已成功采集过, THE System SHALL走增量分支，不重复执行历史回填.
4. The System SHALL keep Telegram 的整数游标行为不变.

### Requirement 4: 媒体与正文

**User Story:** AS 管理员, I want 封面与可选正文进入资讯流, so that 浏览与 AI 判定都有足够信息。

#### Acceptance Criteria

1. The System SHALL转存文章封面图 as条目媒体，沿用既有 pending→ready 异步转存链路.
2. WHILE 摘要模式, THE System SHALL not call `fetch_article_detail`.
3. WHILE 全文模式, THE System SHALL call `fetch_article_detail` for每篇新文章并保存正文文本与正文图片.
4. The System SHALL send微信公众号图片所需的 `Referer: https://mp.weixin.qq.com/` 头 WHEN 下载媒体.
5. The System SHALL allow微信图片主机（`mmbiz.qpic.cn` 等）在媒体下载白名单内.

### Requirement 5: 前端多渠道入口

**User Story:** AS 管理员, I want 在添加渠道时选择类型, so that Telegram 与微信渠道能统一管理。

#### Acceptance Criteria

1. WHEN 管理员打开「添加渠道」, THE System SHALL provide渠道类型选择（Telegram / 微信公众号）.
2. The System SHALL send所选 kind to 预览与创建接口.
3. The System SHALL display渠道类型 label on渠道列表项.
4. WHEN 预览失败, THE System SHALL show a分类化错误提示且不创建渠道.

### Requirement 6: 成本与稳定性

**User Story:** AS 管理员, I want 上游调用可控, so that 成本与失败不影响主流程。

#### Acceptance Criteria

1. The System SHALL bound首次回填的页数与条数.
2. The System SHALL use公众号接口一个不小于 30 秒的超时.
3. IF 上游超时或 5xx, THEN THE System SHALL保留游标并在后续轮次按退避重试.
4. The System SHALL NOT log或返回 TikHub API Key.
5. The System SHALL record每轮采集的 fetched/created/skipped for审计.
