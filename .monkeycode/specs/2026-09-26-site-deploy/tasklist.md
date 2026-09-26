# 需求实施计划：MCP 广场站点部署（Site Deploy）

依据：`requirements.md`、`design.md`（2026-09-26-site-deploy）

- [x] 1. 配置、依赖与数据模型
  - [x] 1.1 在 `backend/app/config.py` 增加 `site_deploy_path`、`site_max_archive_bytes`、`site_max_total_bytes`、`site_max_file_bytes`、`site_max_files`、`site_max_ratio`、`site_max_versions`、`site_retention_days`、`site_mcp_max_bytes` 与 `resolved_site_deploy_path`（对应 design Components §1 与 config 样板）
  - [x] 1.2 在 `.env.example` 同步上述配置项注释
  - [x] 1.3 在 `backend/app/models.py` 新增 `Site`、`SiteVersion`，字段与索引按 design Data Models；`content_hash` 为普通索引，`site_id + version_no` 唯一约束
  - [x] 1.4 在 `backend/app/schemas.py` 补充站点/版本/进度 Pydantic 响应模型
  - [x]* 1.5 单元测试：slug 正则、设置路径解析、版本状态枚举（`test_sites.py` 起步）

- [x] 2. 归档安全校验与落盘（`capabilities/site/archive.py`、`storage.py`）
  - [x] 2.1 实现 `inspect(archive_bytes, entry)`：只接收 `.zip`，逐条拒绝绝对路径、`..`、反斜杠、盘符、符号链接
  - [x] 2.2 实现上限校验：归档字节、文件数、单文件字节、解压总字节、压缩比 `SITE_MAX_RATIO`
  - [x] 2.3 实现顶层目录归一化与入口定位（显式 entry / 根 `index.html` / 唯一 `.html`）
  - [x] 2.4 实现归一化 `content_hash`（排序后的「相对路径 + 文件内容 sha256」）
  - [x] 2.5 实现 `storage`：解包到 `.tmp/{version_id}/`，成功后原子 `os.replace` 到 `{site_id}/v{version_no}/`；`.upload/{version_id}.zip` 暂存与清理
  - [x]* 2.6 测试：合法包解包成功；`../evil`、绝对路径、符号链接、超大压缩比逐条失败且无落盘；顶层目录归一化

- [x] 3. 站点服务与异步流水线（`capabilities/site/sites.py`）
  - [x] 3.1 实现 `create_deploy`：快速校验、定位/新建站点、分配 `version_no`、写 `unpacking` 行并 commit、暂存归档、返回 `(site, version)`
  - [x] 3.2 实现 `run_deploy`：解包、校验、摘要、落盘，按结果置 `ready` / `failed` / `duplicate`，更新 `stage` / `percent` / `message`
  - [x] 3.3 实现去重：命中同站点 `ready` 且同 `content_hash` 时置 `duplicate` 并指向 `reused_version_id`，必要时切换当前版本
  - [x] 3.4 实现 `rollback`（仅 `ready` 且未清理）、`retry`（仅 `failed` 且归档仍在）、`delete_site`、`delete_version`（当前版本拒绝）
  - [x] 3.5 实现 `list_sites`（协议面按 `mcp_key_id` 过滤，管理端全量）与 `version_status` 查询
  - [x]* 3.6 测试：连续部署版本号递增；回滚预览返回旧内容；失败不改变当前版本；去重不新增版本目录；重启恢复把残留 `unpacking` 置 `failed`

- [x] 4. 预览托管与访问控制（`capabilities/site/hosting.py`）
  - [x] 4.1 实现 `serve(slug, path, request)`：站点/当前版本解析，停用或不存在 404，当前版本缺失或 `purged` 503
  - [x] 4.2 实现令牌门禁：Cookie HMAC 校验（绑定 `site_id + access_token_hash`）、`?token=` 校验并回落写 Cookie、失败 401 令牌输入页
  - [x] 4.3 实现路径归一化、文件定位、SPA 回退、`Content-Type` 推断、`no-cache`/长缓存与 `X-Content-Type-Options: nosniff`
  - [x] 4.4 在 `main.py` 于 SPA 兜底之前注册 `/sites/{slug}` 与 `/sites/{slug}/{path}`（参考 `main.py:332`）
  - [x]* 4.5 测试：public 直接访问；token 无凭证 401 / 错误 401 / 正确通过并下发 Cookie / 重置后旧 Cookie 失效；SPA 深链回退；路径穿越拒绝

- [x] 5. Provider 与能力注册（`capabilities/site/provider.py`）
  - [x] 5.1 定义 `CapabilitySpec(capability_id="site", admin_path="/mcp-plaza/sites", icon="globe")`
  - [x] 5.2 实现 MCP tools：`site_deploy`、`site_list`、`site_status`、`site_rollback`、`site_delete`，`site_deploy` 内联 `create_deploy` + `await run_deploy` 返回终态
  - [x] 5.3 实现 `dispatch` 的 operation 分派与 `SiteError`；`site_deploy` base64 超 `SITE_MCP_MAX_BYTES` 返回 400
  - [x] 5.4 在 `registry.ensure_defaults()` 追加 `register(SiteProvider())`（参考 `registry.py:76`）
  - [x] 5.5 在 `runtime._normalize_error` 的 isinstance 元组加入 `SiteError`（参考 `runtime.py:18`）
  - [x]* 5.6 测试：`tools/list` 授权可见 / 未授权不可见；`tools/call` 终态与 REST 一致；base64 超限 400

- [x] 6. 后端 REST 与 Admin 路由
  - [x] 6.1 新建 `capabilities_site_public.py`：`POST /v1/sites`（multipart，202 + version_id）、`GET /v1/sites`、`GET /v1/sites/{slug}`、`GET /v1/sites/{slug}/versions/{version_no}`、`POST /v1/sites/{slug}/rollback`、`DELETE /v1/sites/{slug}`；复用 `_mcp_key_dep` 与 `allowed_capability_ids`
  - [x] 6.2 新建 `admin_mcp_sites.py`：列表、部署（202）、详情、版本状态、`PATCH`、删除、上传新版本、`activate`、`retry`、版本删除、令牌生成/重置
  - [x] 6.3 在 `main.py` 注册上述两个 router（在 `capabilities_public` 之前）
  - [x]* 6.4 测试：未授权 Key 403、`sk-` 401、跨 Key 404；管理端部署返回 202 并可轮询到 `ready`；令牌明文仅生成响应出现，列表为掩码

- [x] 7. 后台保留与启动恢复
  - [x] 7.1 新建 `services/site_retention.py`：每日清理超出 `SITE_MAX_VERSIONS` / `SITE_RETENTION_DAYS` 的非当前终态版本（文件删除、`purged=1`、保留行），清理 `.upload/.tmp` 残留
  - [x] 7.2 新建 `reconcile_stuck_site_versions`：启动时把残留 `unpacking` 置 `failed`（参考 `reconcile_stuck_knowledge_jobs`，`main.py:101`）
  - [x] 7.3 在 `main.py` lifespan 注册 `site_retention_loop()` 与 reconcile 调用
  - [x]* 7.4 测试：伪造多版本触发清理，断言非当前旧版本 `purged=1`、当前版本保留；`unpacking` 不被清理

- [x] 8. 前端 MCP 广场站点页
  - [x] 8.1 `McpPlaza.tsx` 的 `iconMap` 增加 `globe: Globe`
  - [x] 8.2 `App.tsx` 懒加载并注册 `mcp-plaza/sites` 与 `mcp-plaza/sites/:siteId`
  - [x] 8.3 `lib/api.ts` 封装站点/版本/令牌接口与 multipart 上传
  - [x] 8.4 `McpSites.tsx`：站点列表、新建/上传对话框、`unpacking` 进度徽标
  - [x] 8.5 `McpSiteDetail.tsx`：预览地址、版本时间线（状态、设为当前、重试、删除）、访问模式与令牌管理
  - [x] 8.6 上传进度用 `XMLHttpRequest.upload.onprogress`；202 后每 1s 轮询版本状态直到终态并提示结果
  - [x] 8.7 PC 双栏、移动端上下堆叠，对齐现有 Skills/Keys 风格
  - [x]* 8.8 `cd frontend && npx tsc -b` 通过

- [x] 9. 文档与接入说明
  - [x] 9.1 `McpDocs.tsx` 增加 Site Deploy 小节：端点、Header、`site_*` 工具与部署-轮询顺序
  - [x] 9.2 README 增加站点部署与配置项简要说明
  - [x] 9.3 确认 `.env.example` 与 design 配置项一致

- [x] 10. 检查点 — 全量回归
  - [x] 10.1 后端 `PYTHONPATH=. python3 -m pytest tests/test_sites.py` 全绿
  - [x] 10.2 手工验证：上传 zip 得到预览地址、版本回滚、令牌门禁、MCP `site_deploy` 可部署
