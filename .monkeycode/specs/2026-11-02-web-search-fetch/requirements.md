# 联网搜索与网页解析（Web Search & Fetch）

Feature Name: 2026-11-02-web-search-fetch
Updated: 2026-11-02

## Description

为 MCP 广场补上「联网搜索 + 网页解析」这一类高频基础能力。目标是让任意接入的 Agent 通过一组 `web_*` 工具完成「检索 → 抓取 → 正文 → 引用」的闭环，返回可直接进上下文的干净 Markdown。

现状结论：

- 网页解析所需的下游能力已具备：markitdown 已支持 `.html/.htm`（`backend/app/capabilities/docparse/convert.py:16`），SSRF 防护在 `backend/app/info/urlguard.py:57`，统一 HTTP 客户端在 `backend/app/offline/http.py:19`，HTML 白名单清洗在 `backend/app/info/sanitize.py:16`。
- 搜索没有现成基座：`knowledge` 的 FTS5 + 向量只索引用户上传的知识库；`info_items` 有内容池但没有 FTS 索引。
- 通用全网搜索需要第三方数据源。自建全网爬虫索引不在本项目的合理成本范围内。

## Scope

| 决策项 | 结论 |
|--------|------|
| 网页解析 | 本轮实现，作为 `web_fetch` 工具 |
| 私域搜索 | 本轮实现，索引 `info_items`，零外部依赖 |
| 全网搜索 | 本轮实现接入层，默认自托管 SearXNG provider，可替换 |
| 排序 | 本地结果优先，不足时补外部结果，RRF 融合 |
| 引用 | 每条结果必须带 `url`、`title`、`published_at`（可空） |
| MCP Key 隔离 | 沿用现有 `McpKey` + 能力白名单机制 |

## Requirements

### R1 网页抓取

WHEN 调用方提交一个 http/https URL，系统 SHALL 抓取页面并返回结构化正文。

- 只接受 http/https。
- 拒绝私有、回环、链路本地、保留、组播地址。
- 限制响应大小、超时、重定向跳数。
- 按 `robots.txt` 决定是否允许抓取，命中禁止时返回明确错误。
- 返回字段至少包含 `url`、`final_url`、`title`、`published_at`、`markdown`、`truncated`。
- URL 指向 PDF/Office 时转交 docparse 管线处理。

### R2 正文提取

WHEN 页面为 HTML，系统 SHALL 先提取正文再转 Markdown，避免导航、页脚、广告进入上下文。

- 正文提取失败时降级为整页清洗后转 Markdown，并标记 `extract_fallback=true`。

### R3 私域搜索

WHEN 调用方提交查询词，系统 SHALL 在 `info_items` 上执行全文检索。

- 使用 SQLite FTS5 + BM25，按 `published_at` 支持时间过滤。
- 中文必须支持两字词检索（如 `开门`、`限流`）。实测 `unicode61` 与 `trigram` 都无法命中两字词，采用字符二元组索引。
- 结果带来源、标题、摘要、原文链接。

### R4 全网搜索

WHEN 私域结果不足或调用方显式要求联网，系统 SHALL 调用外部搜索 provider。

- provider 必须可替换，至少有 `searxng` 与 `disabled` 两种实现。
- 单个 provider 失败不得导致整体失败，需返回部分结果和 `warnings`。

### R5 统一输出

WHEN 返回结果，系统 SHALL 使用统一结构，便于 Agent 直接引用。

- 每个结果包含 `title`、`url`、`snippet`、`source`、`published_at`、`score`、`provider`。

## Non-Goals

- 不自建全网爬虫与索引。
- 不做浏览器渲染（JS 重度站点本轮不支持）。
- 不做登录态站点抓取。
- 不承诺搜索结果与商业搜索引擎一致。

## Acceptance

- `web_fetch` 对一个静态文章页返回干净 Markdown，正文不含导航与页脚。
- `web_fetch` 对内网地址、非 http 协议、超限响应返回明确错误码。
- `web_search` 在未配置外部 provider 时仍能返回私域结果。
- `web_search` 在外部 provider 超时或报错时返回私域结果并附 `warnings`。
- 每个工具都经过 `mcp_key` 能力白名单校验。
