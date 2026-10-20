# 需求实施计划

- [ ] 1. 建立安全中心数据层
  - [ ] 1.1 在 `backend/app/models.py` 新增 `SecurityEvent` 模型
    - 按设计文档「Data Models」定义全部字段：event_type / severity / source / actor / attempted_password_encrypted / client_ip / ip_location / location_trusted / location_rule / user_agent / client_name / method / path / status_code / risk_tags / detail_json / created_at
    - 为 created_at 建索引；risk_tags、detail_json 用 Text 存 JSON（需求 1.1、1.3、1.4；设计「SecurityEvent」）

  - [ ] 1.2 在 `backend/app/models.py` 新增 `SecuritySettings` 单行配置模型
    - 字段：id、location_check_enabled、trusted_locations_json、ip_allowlist_json、updated_at（需求 8.1、8.12；设计「SecuritySettings」）

  - [ ] 1.3 在 `backend/app/db.py` 增加建表与索引迁移
    - 新增 `_ensure_security_columns`：`CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`（created_at、event_type+created_at、client_ip+created_at、severity+created_at）
    - 幂等插入 `security_settings` 默认行（id=1，location_check_enabled=true，空白名单），并在 `init_db` 中调用（需求 1.4、6.1；设计「Data Models」）

  - [ ] 1.4 在 `backend/app/config.py` 新增 security 配置项
    - `security_audit_enabled`、`security_beacon_enabled`、`security_event_retention_days`、`security_dedupe_window_seconds`、`security_max_password_len`、`security_beacon_rate_limit`、`security_auth_fail_threshold`、`security_auth_fail_window_seconds`
    - 在 `.env.example` 补充同名变量与注释（设计「10. 配置项」）

  - [ ]* 1.5 为数据层编写单元测试
    - `backend/tests/test_security_audit.py`：init_db 后两张表存在、索引存在、默认 SecuritySettings 行字段正确
    - 验证迁移可重复执行不报错（幂等）

- [ ] 2. 实现判定与匹配服务 `backend/app/services/security_audit.py`
  - [ ] 2.1 实现请求判定 `judge_request` 与常量
    - 定义 `PROXY_PREFIXES`、`CONSOLE_PREFIXES`、`BROWSER_TOKEN`、`SCANNER_PATTERNS`、`looks_like_browser`
    - 产出 `JudgeResult`（is_console / is_proxy / source / risk_tags / severity / client_ip / user_agent / client_name），Proxy_Route 直接 source=api 且不判定
    - 依次叠加 `non_browser_ua`、`missing_marker`、`origin_mismatch`、`scanner_ua`（需求 3.1–3.6、3.10、3.11；设计「3. 判定规则」）

  - [ ] 2.2 实现自动化信号 `automation_signals`
    - 覆盖 `headless_ua`、`missing_accept_language`、Beacon `webdriver`、`zero_screen`；命中即返回信号名列表（需求 3.7、3.8；设计「3.1 自动化信号」）

  - [ ] 2.3 实现地点归一化与匹配 `normalize_location` / `match_location` / `classify_location`
    - 行政后缀归一化、直辖市 province==city；三级粒度命中；CIDR 白名单短路；内网→`local_network`、未知→`location_unknown`、未命中→`unfamiliar_location`、空配置全判未命中（需求 8.2–8.11；设计「3.2 Location_Match」）

  - [ ] 2.4 实现节流、内存队列与入队 API
    - `_LAST_EMIT` 按 `(event_type, ip, path, status)` 指纹节流；`maybe_enqueue`、`enqueue_login_event`、`enqueue_frontend_event`、`record_auth_failure`（滑窗计数，超阈值生成 `auth_fail_burst`）
    - 队列上限 maxsize，满则丢弃并打日志（需求 3.9；设计「4. 队列与 worker」）

  - [ ] 2.5 实现 `audit_worker` 落库
    - 取出事件 → `client_ip` → `lookup_ip_location`（事务外）→ `load_trusted_locations` 快照 → 追加地点与自动化标签 → 失败密码 `encrypt_secret` 加密限长 → 独立短事务写库（需求 2.3、2.4、8.3–8.6；设计「4. 队列与 worker」）

  - [ ] 2.6 实现 `load_trusted_locations` 读取缓存
    - 读取并缓存 `security_settings`，带 TTL；提供 `invalidate_trusted_locations()` 供保存设置后失效（需求 8.1、8.12；设计「SecuritySettings」）

  - [ ]* 2.7 为判定/匹配/信号编写单元与属性测试
    - `test_security_audit.py`：judge_request 各分支；automation_signals 四类信号；normalize_location/match_location/classify_location 三级命中与边界
    - 属性测试：设计正确性属性 1（Proxy_Route 永不带 non_browser_ua/missing_marker/probe_path）、属性 6（地点判定确定性）
    - 节流属性：同指纹窗口内仅一条（设计「Test Strategy」）

- [ ] 3. 接线中间件与生命周期
  - [ ] 3.1 在 `backend/app/main.py` 新增 `SecurityAuditMiddleware`
    - 纯 ASGI，读 header/path/peer 调 `judge_request`，用 send_wrapper 捕获 status 后 `maybe_enqueue`；不做 body 改写、不 await 外呼
    - 注册在 `DisableApiCacheMiddleware` 同类层级，`security_audit_enabled=false` 时旁路（需求 3.11；设计「2. 中间件」）

  - [ ] 3.2 在 `lifespan` 中启动 audit worker
    - `asyncio.create_task(audit_worker())` 并纳入统一 cancel 列表（设计「4. 队列与 worker」）

  - [ ]* 3.3 编写中间件行为测试
    - 构造无 marker 的 `/api/admin/health` 请求断言入队 `missing_marker`；构造 `/v1/chat/completions` 断言不入队（设计「Test Strategy」）

- [ ] 4. 改造登录审计 `backend/app/routers/admin_auth.py`
  - [ ] 4.1 在 `login` 中记录认证事件
    - 增加 `request: Request`，用 `client_meta` 取 context；成功写 `login_success`（无密码）、失败写 `login_failure`（带密码）、锁定写 `login_locked`（带密码）
    - 失败时调用 `record_auth_failure`；不改变既有响应与限流行为（需求 2.1、2.3–2.6、2.8；设计「5. 登录审计改造」）

  - [ ] 4.2 在 `update_me` 中记录 `admin_credential_change`
    - 改用户名或改密成功后写入 warn 级事件（需求 2.7）

  - [ ]* 4.3 编写登录审计测试
    - `backend/tests/test_admin_security_api.py`：成功/失败/锁定各写入对应事件；失败事件密码可解密还原且被限长；成功事件密码列为空（设计「Test Strategy」）

- [ ] 5. 实现安全中心 API `backend/app/routers/admin_security.py`
  - [ ] 5.1 实现事件列表与详情接口
    - `GET /api/admin/security/events`（type/severity/source/ip/q/since/until + 分页，**不返回** attempted_password*）
    - `GET /api/admin/security/events/{id}`：失败/锁定事件解密返回 `attempted_password`，解密失败返回 null 且标记 `password_unavailable`（需求 2.5、5.3–5.7；设计「9. 后端接口」）

  - [ ] 5.2 实现概览接口 `GET /api/admin/security/overview`
    - 登录成功/失败、异常请求、独立来源 IP 计数与趋势（需求 5.2）

  - [ ] 5.3 实现 Beacon 接口 `POST /api/admin/security/beacon`
    - 管理员鉴权 + 单管理员窗口限流（超限静默 204）；计算自动化信号决定 severity 并入队 `frontend_beacon`（需求 4.1–4.6；设计「6. Beacon 接口」）

  - [ ] 5.4 实现设置读写接口
    - `GET` / `PUT /api/admin/security/settings`：归一化校验常用地点与 IP/CIDR 白名单，整体替换保存，保存后失效缓存（需求 8.1、8.12）

  - [ ] 5.5 在 `schemas.py` 补 DTO 并在 `main.py` 注册 router
    - 新增事件列表/详情/概览/Beacon/设置 的请求响应模型；`app.include_router(admin_security.router)`（设计「9. 后端接口」）

  - [ ]* 5.6 编写安全中心接口测试
    - 未认证访问 401；Beacon 超限静默；列表不含密码字段；设置保存后命中/未命中标签随配置变化；过滤与分页正确（设计「Test Strategy」）

- [ ] 6. 实现保留期清理 `backend/app/services/security_retention.py`
  - [ ] 6.1 实现每日清理循环并在 `lifespan` 接线
    - 删除早于 `security_event_retention_days` 的 `security_events`，记录删除条数；`main.py` 中随其余后台任务 cancel（需求 6.1、6.2；设计正确性属性 7）

  - [ ]* 6.2 编写保留清理测试
    - 造旧/新事件，断言仅旧事件被删、`security_settings` 不受影响

- [ ] 7. 检查点 - 确保后端测试全部通过
  - 运行 `cd backend && PYTHONPATH=. python3 -m pytest tests/test_security_audit.py tests/test_admin_security_api.py`，如有疑问请询问用户

- [ ] 8. 实现前端请求标识与埋点
  - [ ] 8.1 在 `frontend/src/lib/api.ts` 注入 `X-GW-Client: web`
    - 在 `request()` 的 headers 统一加上该标识，作为前端正规请求判据（设计「5. 前端标识」）

  - [ ] 8.2 新增 `frontend/src/lib/security.ts`
    - 实现 `clientEnv()`（timezone/language/screen/dpr/platform/webdriver）、`track()`、缓冲与 `flush()`（`keepalive` fetch 带 Authorization）、`pagehide` 监听（需求 4.1–4.3；设计「7. 前端埋点 SDK」）

  - [ ] 8.3 在 `App.tsx` 接线登录/登出埋点
    - 登录成功后 `track('session_start')`；`clearToken` 前 `track('logout')` 并 `flush()`（需求 2.5、4.1）

- [ ] 9. 实现安全中心页面 `frontend/src/pages/Security.tsx`
  - [ ] 9.1 注册路由与导航
    - `App.tsx` 懒加载 `/security`；`nav.ts` 平台分组新增「安全中心」项与关键词（需求 5.1；设计「8. 页面」）

  - [ ] 9.2 实现概览卡与事件趋势
    - 调用 `/api/admin/security/overview`，复用 `components/charts`（需求 5.2）

  - [ ] 9.3 实现登录/异常/埋点/全部四个 Tab 列表
    - 复用 `filter-bar`、分页与 `ui.tsx`；支持 since/until/type/severity/source/ip/q；异常视图展示 Risk_Tag 与级别；双端响应式（需求 5.3–5.5、5.7、5.8）

  - [ ] 9.4 实现事件详情抽屉
    - 展示完整 `detail_json`、Risk_Tag、自动化信号；失败/锁定事件提供「显示本次尝试密码」按钮按需请求详情（需求 5.6、2.5）

  - [ ] 9.5 实现设置 Tab
    - 增删常用地点（国家/省/省市三级）与 IP/CIDR 白名单、地点判定开关，保存调 PUT `/settings`（需求 8.1、8.9、8.12）

- [ ] 10. 检查点 - 前端类型检查与联调通过
  - 运行 `cd frontend && npx tsc -b`，启动前后端确认 `/security` 可用；如有疑问请询问用户
