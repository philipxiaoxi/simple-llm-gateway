# LLM 中转站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地一台自用/可发 Key 的 LLM 中转站：OpenAI + Anthropic 双协议、三个上游预设、管理后台可探测/看额度。

**Architecture:** FastAPI 同时挂网关与 `/api/admin`。下游 Key 绑死一个上游账号。协议互转只调用 LiteLLM。SQLite 存账号、Key（Fernet 可逆）、OAuth token、请求日志。React 管理端生产时由 FastAPI 托管 `dist`。

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite, LiteLLM, cryptography, httpx, Vite, React, TypeScript, Tailwind, shadcn/ui, TanStack Query, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-14-llm-gateway-design.md`

---

## File map

```
backend/app/main.py                 应用入口、挂路由、托管前端
backend/app/config.py               环境变量
backend/app/db.py                   引擎、Session、建表
backend/app/models.py               SQLAlchemy 模型
backend/app/crypto.py               Fernet 加解密、Key 哈希
backend/app/schemas.py              Pydantic
backend/app/deps.py                 管理员 JWT、下游 Key
backend/app/errors.py               OpenAI / Anthropic 错误体
backend/app/seed.py                 空库种管理员
backend/app/routers/health.py
backend/app/routers/admin_auth.py
backend/app/routers/admin_accounts.py
backend/app/routers/admin_keys.py
backend/app/routers/admin_logs.py
backend/app/routers/admin_dashboard.py
backend/app/routers/oauth.py
backend/app/routers/proxy.py         全部网关别名
backend/app/services/proxy.py        转发与记日志
backend/app/services/bridge.py       LiteLLM 封装
backend/app/services/probe.py
backend/app/services/quota.py
backend/app/services/grok_oauth.py
backend/app/providers.py             三个预设
backend/tests/...
frontend/                            Vite React 管理端
docker-compose.yml
Dockerfile
.env.example
README.md
```

---

### Task 1: 仓库骨架与加密

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/crypto.py`
- Create: `backend/tests/test_crypto.py`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_crypto.py`：同一密钥加解密往返；不同密钥解不开；`hash_api_key` 对同一明文稳定且不等于明文。

- [ ] **Step 2: 跑测试，确认失败**

```bash
cd backend && python -m pytest tests/test_crypto.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

`APP_SECRET_KEY` 经 SHA-256 再 urlsafe_b64 得到 Fernet key。`encrypt_secret` / `decrypt_secret`。`hash_api_key` 用 sha256 hex。`generate_api_key` 返回 `sk-` + 32 字节 urlsafe。

- [ ] **Step 4: 测试通过并提交**

```bash
git add backend .env.example .gitignore
git commit -m "feat: 增加密钥加解密与 API Key 哈希"
```

---

### Task 2: 数据库模型与空库种管理员

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Create: `backend/app/seed.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_seed.py`

- [ ] 表：`admins`、`upstream_accounts`、`oauth_tokens`、`oauth_states`、`api_keys`、`request_logs`，字段与 spec §5 一致。
- [ ] `conftest.py` 用临时 SQLite + 覆盖 `DATABASE_PATH`、`APP_SECRET_KEY`。
- [ ] 测试：空库 `seed_admin` 后能用密码哈希校验登录；再调用不会重复插入。
- [ ] Commit: `feat: 增加 SQLite 模型与管理员初始化`

---

### Task 3: 管理员登录与鉴权依赖

**Files:**
- Create: `backend/app/schemas.py`
- Create: `backend/app/deps.py`
- Create: `backend/app/routers/admin_auth.py`
- Create: `backend/app/main.py`（先只挂 health + login）
- Create: `backend/app/routers/health.py`
- Create: `backend/tests/test_admin_auth.py`

- [ ] `POST /api/admin/login` 成功返回 `{token, username}`；错误密码 401。
- [ ] `GET /health` 返回 `{status: ok}`。
- [ ] `GET /api/admin/me` 无 token 401，有 token 返回用户名。
- [ ] JWT：HS256，`sub=username`，过期 7 天。
- [ ] Commit: `feat: 增加管理员登录与健康检查`

---

### Task 4: 上游账号 CRUD、探测、额度

**Files:**
- Create: `backend/app/providers.py`
- Create: `backend/app/services/probe.py`
- Create: `backend/app/services/quota.py`
- Create: `backend/app/routers/admin_accounts.py`
- Create: `backend/tests/test_accounts.py`

预设：

```python
PRESETS = {
  "opencode_go": {"auth_type": "api_key", "base_url": "https://opencode.ai/zen/go", "label": "OpenCode Go"},
  "grok": {"auth_type": "oauth", "base_url": "https://api.x.ai/v1", "label": "Grok"},
  "deepseek": {"auth_type": "api_key", "base_url": "https://api.deepseek.com", "label": "DeepSeek"},
}
```

- [ ] `GET /api/admin/providers` 返回预设。
- [ ] `POST /api/admin/accounts` 创建；api_key 加密入库；列表不回完整密钥，详情可 `?reveal=1` 解密。
- [ ] `POST /api/admin/accounts/{id}/probe`：httpx GET `{base}/models` 或 `{base}/v1/models`，写 `last_probe_*`。测试用 httpx mock。
- [ ] `POST /api/admin/accounts/{id}/quota`：DeepSeek 打 `/user/balance`；其余尽力，不支持则 `supported: false`。测试 mock。
- [ ] Commit: `feat: 增加上游账号、手动探测与额度查询`

---

### Task 5: 下游 API Key

**Files:**
- Create: `backend/app/routers/admin_keys.py`
- Create: `backend/tests/test_keys.py`

- [ ] 创建必须带 `account_id`，返回完整 `sk-` 一次。
- [ ] `GET /api/admin/keys/{id}` 含解密后的完整 key。
- [ ] 停用 / 删除。不能改 `account_id`。
- [ ] Commit: `feat: 增加下游 API Key 的创建与明文回显`

---

### Task 6: 网关鉴权与错误体

**Files:**
- Create: `backend/app/errors.py`
- Create: `backend/app/routers/proxy.py`（先鉴权 + 401/403）
- Create: `backend/tests/test_proxy_auth.py`

- [ ] 无效 Key：OpenAI 路径返回 `{"error":{"message":"...","type":"invalid_request_error"}}` 401。
- [ ] Anthropic 路径返回 `{"type":"error","error":{"type":"authentication_error","message":"..."}}` 401。
- [ ] 停用 Key 同样 401；账号停用 403。
- [ ] `Authorization: Bearer` 与 `x-api-key` 都能通过。
- [ ] Commit: `feat: 增加网关 Key 鉴权与双协议错误体`

---

### Task 7: LiteLLM 转发、日志、流式

**Files:**
- Create: `backend/app/services/bridge.py`
- Create: `backend/app/services/proxy.py`
- Modify: `backend/app/routers/proxy.py`
- Create: `backend/tests/test_proxy_forward.py`

- [ ] 所有网关别名挂到同一 handler。
- [ ] `bridge` 把上游一律当 OpenAI 兼容：`acompletion` / `aresponses`，`api_base` + `api_key`（Grok 用 access token）。
- [ ] 入口 Anthropic 时用 LiteLLM 的 anthropic messages 接口或等价转换，响应按 Anthropic 回。
- [ ] 测试：mock `bridge.forward`，断言请求打到绑定账号、日志写入请求、流式结束后 `response_body` 完整。
- [ ] `GET /v1/models` 按账号返回静态/探测到的模型列表；没有则返回预设默认模型。
- [ ] `count_tokens` 用 `litellm.token_counter`，回 `{"input_tokens": N}`。
- [ ] Commit: `feat: 用 LiteLLM 转发双协议并写入审计记录`

---

### Task 8: Grok OAuth

**Files:**
- Create: `backend/app/services/grok_oauth.py`
- Create: `backend/app/routers/oauth.py`
- Create: `backend/tests/test_grok_oauth.py`

- [ ] `GET /api/admin/accounts/{id}/oauth/start` 生成 PKCE，存 `oauth_states`，返回 `authorize_url`。
- [ ] `GET /api/admin/oauth/grok/callback` 换 token，加密写入 `oauth_tokens`，重定向到 `{APP_BASE_URL}/accounts?oauth=ok`。
- [ ] 转发前若 `expires_at` 将过期则 refresh。
- [ ] 测试 mock token 端点。
- [ ] Commit: `feat: 增加 Grok xAI OAuth 授权与刷新`

---

### Task 9: 概览与记录审计 API

**Files:**
- Create: `backend/app/routers/admin_dashboard.py`
- Create: `backend/app/routers/admin_logs.py`
- Create: `backend/tests/test_logs.py`

- [ ] `GET /api/admin/dashboard`：账号数、异常数（探测失败或 Grok 无 token）、今日请求、今日失败。
- [ ] `GET /api/admin/logs`：筛选 `account_id`、`api_key_id`、`protocol`、`status`、`model`、时间。分页。
- [ ] `GET /api/admin/logs/{id}`：含 `request_body`、`response_body`。
- [ ] Commit: `feat: 增加概览与记录审计查询`

---

### Task 10: React 管理后台

**Files:**
- Create: `frontend/` Vite React TS 工程
- shadcn + Tailwind；深色值机台：锌底、lime 成功、amber 警告、rose 失败；IBM Plex Sans / Mono
- 页面：`/login` `/` `/accounts` `/keys` `/logs` `/logs/:id`
- 手机：顶栏菜单 + 卡片列表
- Vite proxy 到 `localhost:8000`

- [ ] 登录、账号 CRUD、探测、额度、Grok 授权跳转
- [ ] Key 创建与显示完整 sk
- [ ] 日志列表 + 消息气泡详情
- [ ] Commit: `feat: 增加管理员响应式控制台`

---

### Task 11: 托管前端、Docker、README

**Files:**
- Modify: `backend/app/main.py` 挂 StaticFiles（`frontend/dist` 存在时）
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `README.md`

- [ ] 单容器：先 build 前端再装后端。数据卷 `./data`
- [ ] README 写清环境变量与三个客户端的 base_url 填法
- [ ] 跑完全部 pytest
- [ ] Commit: `feat: 增加 Docker 部署与使用说明`

---

## Spec coverage

| Spec | Task |
|------|------|
| 双协议路径与别名 | 6, 7 |
| 三个上游预设 | 4 |
| Key 绑死账号、可回显明文 | 5 |
| 手动探测 | 4 |
| 上游额度 | 4 |
| 请求日志 | 7, 9, 10 |
| Grok OAuth | 8 |
| PC/手机后台 | 10 |
| Docker + SQLite | 2, 11 |
| LiteLLM 互转 | 7 |

## 自审

- 无 TBD。下游 Key 同时存 hash（查找）与密文（回显），与「可以再看明文」一致。
- 类型名统一：`opencode_go` / `grok` / `deepseek`；协议 `openai_chat` / `openai_responses` / `anthropic_messages`。
- 第一版范围与 spec §11 对齐，不把 embeddings、分组调度写进任务。
