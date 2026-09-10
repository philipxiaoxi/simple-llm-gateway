# Requirements Document

## Introduction

本站由 FastAPI 单体同时承担三件事：管理后台接口（`/api/admin/*`）、对外网关转发（`/v1`、`/anthropic` 等）、以及前端静态资源托管（`frontend/dist`）。在真实网络环境下，「打开页面慢」主要来自三处可观测的浪费：

1. **静态资源代理没有缓存与压缩策略**：内容哈希产物（`/assets/index-*.js`，561 KB）不带 `Cache-Control`，每次访问都要回源校验；正文不压缩，JS/CSS 全量明文传输；`HEAD` 请求还会错落到 SPA 兜底，返回 `index.html` 的元数据。
2. **后端接口响应体未压缩且存在可消除的重复查询**：单个列表接口正文可达 35–45 KB，gzip 后可压到 1/10；部分端点在同一请求内重复计算同一份数据。
3. **前端首屏是一个大包**：17 个页面全部静态导入，首屏必须下载并解析整包；字体从 `fonts.googleapis.com` 阻塞加载。

本次优化的目标是：在不改变对外协议行为、不降低功能完整性的前提下，显著降低首屏传输量与接口响应耗时，并留下可复现的度量手段。

## Glossary

- **静态资源代理**：FastAPI 托管 `frontend/dist` 的路径集合，含 `/assets/*`、`index.html`、`sw.js`、`manifest.webmanifest`、图标等。
- **内容哈希资源**：文件名内含构建哈希的产物（如 `index-DcRPg1Na.js`），内容变化必然换名。
- **首屏传输量**：首次访问到页面可交互为止，浏览器需要下载的字节数。
- **预压缩产物**：构建阶段生成的同名 `.br` / `.gz` 文件，运行时按 `Accept-Encoding` 直接选取，不需要每请求压缩。
- **流式响应**：网关转发的 SSE（`text/event-stream`），必须逐块下发。
- **基准脚本**：`scripts/perf_bench.py`，对同一批路径重复采样，输出耗时、正文大小与压缩后大小。

## Requirements

### Requirement 1: 静态资源缓存策略

**User Story:** AS 回访用户, I want 浏览器直接复用本地已缓存的前端资源, so that 第二次打开站点不再重新下载 561 KB 的 JS。

#### Acceptance Criteria

1. WHEN 客户端请求 `/assets/` 下带内容哈希的文件, THE 系统 SHALL 返回 `Cache-Control: public, max-age=31536000, immutable`。
2. WHEN 客户端请求 `index.html`、`sw.js`、`manifest.webmanifest`, THE 系统 SHALL 返回 `Cache-Control: no-cache`，并保留 `ETag` 以支持条件请求。
3. WHEN 客户端带着未过期的 `If-None-Match` 请求未变更的资源, THE 系统 SHALL 返回 `304` 且不返回正文。
4. WHEN 客户端对任一静态路径发送 `HEAD`, THE 系统 SHALL 返回与该路径 `GET` 一致的响应头（`Content-Type`、`Content-Length`、`Cache-Control`），SHALL NOT 回落到 SPA 的 `index.html`。
5. WHEN 请求的是未知路径的页面路由, THE 系统 SHALL 继续返回 `index.html`，保持现有前端路由行为不变。

### Requirement 2: 传输压缩

**User Story:** AS 弱网用户, I want 站点用压缩后的体积传输资源, so that 首屏等待时间明显缩短。

#### Acceptance Criteria

1. WHEN 客户端声明支持 `br` 或 `gzip`, THE 系统 SHALL 对文本类响应返回压缩正文，并带上对应的 `Content-Encoding` 与 `Vary: Accept-Encoding`。
2. WHEN 客户端不支持压缩, THE 系统 SHALL 返回未压缩正文，功能不受影响。
3. WHEN `frontend/dist` 下存在预压缩产物, THE 系统 SHALL 优先直接发送预压缩文件，SHALL NOT 每次请求重新压缩。
4. WHEN 响应为 `text/event-stream`（网关流式转发）, THE 系统 SHALL 不压缩、不缓冲，保持逐块下发与首字延迟不变。
5. THE 系统 SHALL NOT 改变响应正文内容（解压后与优化前逐字节一致）。

### Requirement 3: 后端接口响应优化

**User Story:** AS 管理员, I want 后台页面打开与翻页更快, so that 日常巡检不用等接口。

#### Acceptance Criteria

1. WHEN 管理端列表接口命中压缩, THE 系统 SHALL 使正文传输量下降到优化前的 20% 以内。
2. WHEN 同一请求内需要同一份数据多次, THE 系统 SHALL 只查询/计算一次。
3. WHEN 列表接口返回记录, THE 系统 SHALL 只返回列表展示所需字段，大字段（请求正文、响应正文、reasoning 等）按详情接口按需拉取。
4. WHEN 查询带筛选或排序条件, THE 系统 SHALL 有对应索引支撑，且分页深度不影响单页耗时。
5. WHEN 数据为半静态（分类、目录、快照等）, THE 系统 SHALL 在有效期内复用缓存结果。

### Requirement 4: 前端首屏体积

**User Story:** AS 站点访客, I want 只下载当前页面需要的代码, so that 首屏更快可用。

#### Acceptance Criteria

1. WHEN 用户访问任一页面, THE 系统 SHALL 只加载该路由所需代码（路由级懒加载），其余页面按需加载。
2. WHEN 构建产物拆分, THE 系统 SHALL 将第三方依赖拆为可长期缓存的独立 chunk，业务代码变更不使其失效。
3. WHEN 页面渲染首屏, THE 系统 SHALL NOT 因外部字体请求阻塞渲染。
4. WHEN Service Worker 预缓存, THE 系统 SHALL 覆盖全部构建产物，且不会让用户停留在陈旧版本。
5. WHEN 首屏加载完成, 首屏下载体积 SHALL 相比优化前下降 50% 以上。

### Requirement 5: 度量与回归

**User Story:** AS 维护者, I want 每项优化都有前后对比数据, so that 收益可验证、回退有依据。

#### Acceptance Criteria

1. WHEN 运行 `python3 scripts/perf_bench.py --label <name>`, THE 脚本 SHALL 输出各路径的中位耗时、正文大小与 gzip 后大小，并写入 `docs/perf/<name>.json`。
2. WHEN 传入 `--compare docs/perf/<baseline>.json`, THE 脚本 SHALL 输出逐路径的变化量。
3. WHEN 完成一项优化, THE 提交 SHALL 附带同一脚本的前后对比结果。
4. WHEN 执行 `pytest`, THE 现有 284 条用例 SHALL 全部通过；新增行为 SHALL 有对应用例覆盖。
5. WHEN 执行 `npm run build`, THE 构建 SHALL 成功且 `tsc -b` 无类型错误。
