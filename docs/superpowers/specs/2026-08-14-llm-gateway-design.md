# LLM 中转站设计说明

日期：2026-08-14  
状态：已确认并实现。供应商抽象见 `2026-08-15-provider-abstraction.md`（该文档覆盖本节 §7 与 `GET /v1/models` 的后续约定）。

## 1. 目标

自建 LLM 中转站：对外同时提供 OpenAI 与 Anthropic 协议，把请求转到管理员配置的上游账号。第一版默认三个上游预设：OpenCode Go、Grok（xAI OAuth）、DeepSeek。管理员网页可手动探测上游是否可用、查询上游额度。面向多人时只发 API Key，不开放用户注册。

成功标准：

- 下游用一把 `sk-` Key，能分别走 `/v1/chat/completions` 和 `/v1/messages`，打到该 Key 绑定的上游。
- 管理员能在 PC / 手机网页上手动探测、查看上游额度、随时复制完整下游 Key。
- 协议互转使用 LiteLLM，不手写转换器。

## 2. 使用模型

- 上游账号必做；中转站同时给多人用。
- 网页只有管理员。下游用户不登录，只用 API Key。
- 创建下游 Key 时必须绑死一个上游账号。第一版不分组、不自动换号、不改绑。
- 额度只查上游账号。不做下游限额。
- 健康检查只手动点，不定时巡检。

## 3. 系统结构

两个目录、生产环境一个进程：

- `backend/`：FastAPI。网关 + 管理 API + SQLite。
- `frontend/`：Vite + React + TypeScript + shadcn/ui + Tailwind。管理员响应式网页。

生产时 FastAPI 托管前端 `dist`，只暴露一个端口。开发时 Vite 把 `/v1`、`/anthropic`、`/api`、`/health`、`/chat`、`/responses`、`/models` 代理到后端。

```
客户端（OpenCode / Claude Code / Cursor / Grok CLI）
        │  OpenAI 或 Anthropic 路径 + sk- Key
        ▼
   FastAPI 网关
        │  校验 Key → 解出绑定账号 → LiteLLM 转换转发 → 记日志
        ▼
   OpenCode Go / DeepSeek / Grok(OAuth)
```

管理员 JWT 只用于 `/api/admin/*`。下游 Key 和上游密钥、OAuth token 只存在服务端，Fernet 可逆加密，主密钥是环境变量 `APP_SECRET_KEY`。

## 4. 对外路径

对齐 New API、Sub2API、LiteLLM 的客户端填法：OpenAI 客户端填 `https://站/v1`；Claude Code 填站点根或 `https://站/anthropic`（SDK 自己拼 `/v1/messages`）；Grok CLI / Codex 填 `https://站/v1` 走 Responses。

### 4.1 OpenAI 兼容

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/chat/completions` | 主入口 |
| POST | `/chat/completions` | 别名 |
| GET | `/v1/models` | 返回该 Key 绑定上游的模型列表 |
| GET | `/models` | 别名 |
| POST | `/v1/responses` | Responses API |
| POST | `/responses` | 别名 |

### 4.2 Anthropic 兼容

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/messages` | Base 设成站点根 |
| POST | `/anthropic/v1/messages` | Base 设成 `.../anthropic` |
| POST | `/v1/messages/count_tokens` | Claude Code 会打 |
| POST | `/anthropic/v1/messages/count_tokens` | 带前缀别名 |

### 4.3 管理与探活

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 进程探活，不鉴权 |
| POST | `/api/admin/login` | 管理员登录 |
| * | `/api/admin/*` | 账号、Key、日志、额度、探测、OAuth |

鉴权：`Authorization: Bearer sk-xxx` 与 `x-api-key: sk-xxx` 都收。

第一版不做：`/v1/embeddings`、`/v1/images/*`、`/v1/audio/*`、旧版 `/v1/completions`、`/backend-api/codex/responses`。

## 5. 数据模型（SQLite）

密钥一律可逆加密（Fernet，由 `APP_SECRET_KEY` 派生）。下游 Key 另存 SHA-256 哈希用于鉴权查找，后台仍可解密出完整 `sk-…`。

### `admins`

- `id`、`username`、`password_hash`、`created_at`、`last_login_at`
- 空库启动时用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 种一个账号

### `upstream_accounts`

- `name`、`provider`（`opencode_go` / `grok` / `deepseek`）、`auth_type`（`api_key` / `oauth`）
- `base_url`（按供应商预填，可改）
- `api_key_encrypted`（OpenCode Go / DeepSeek；Grok 为空）
- `status`（`active` / `disabled`）
- `last_probe_ok`、`last_probe_latency_ms`、`last_probe_message`、`last_probe_at`
- `quota_json`、`quota_updated_at`（2026-08-15 起为 `{ok, message?, items[]}`，见供应商抽象文档）
- `models_json`、`models_updated_at`（管理员「获取模型」入库；下游 `GET /v1/models` 只读这份）

### `oauth_tokens`

- `account_id`（一对一，仅 Grok）
- `access_token_encrypted`、`refresh_token_encrypted`、`expires_at`、`scope`、`updated_at`

### `oauth_states`

- OAuth 授权过程中的 `state`、`code_verifier`、`account_id`、`expires_at`

### `api_keys`

- `name`、`key_hash`、`key_encrypted`、`key_prefix`、`account_id`、`status`、`created_at`、`last_used_at`
- 创建时必须绑一个上游账号。第一版不换绑。

### `request_logs`

- `account_id`、`api_key_id`、`protocol`（`openai_chat` / `openai_responses` / `anthropic_messages`）
- `model`、`stream`、`status`、`http_status`、`error_message`
- `prompt_tokens`、`completion_tokens`、`total_tokens`、`latency_ms`
- `request_body`、`response_body`（全文；流式结束后拼回）
- `created_at`
- 索引：`(account_id, created_at)`、`(api_key_id, created_at)`

第一版不加：探测历史表、会话表、下游额度表、多管理员角色。

## 6. 转发流程

1. 按路径识别入口协议。
2. 取出 Key，用哈希查找。无效 / 停用 → `401`，错误体跟入口协议。
3. 读取绑定上游。账号停用或 Grok 刷新失败 → `403` / `502`，不回上游 token。
4. Grok：access token 过期则先 refresh；refresh 失败则标记需重新授权。
5. LiteLLM 按入口协议转换并转发，流式 SSE 边转边推。
6. 写 `request_logs`。没有 usage 就空着，不编数字。
7. 成功按入口协议回；失败把上游信息转成对应协议错误，不回内部堆栈。

| 情况 | HTTP | 客户端 |
|------|------|--------|
| Key 无效 / 停用 | 401 | 协议标准未授权 |
| 绑定账号不可用 | 403 | 账号已停用或授权失效 |
| 上游超时 | 504 | 上游超时 |
| 上游 4xx/5xx | 尽量跟上游 | 转成当前协议 error |
| LiteLLM 转换失败 | 502 | 协议转换失败 |

默认超时 120 秒（`REQUEST_TIMEOUT_SECONDS`）。不做自动重试、不做换号。

## 7. 三个上游预设

> 2026-08-15 起，预设不再是 `PRESETS` 字典，而是 `backend/app/providers/` 下的子类。额度存 `items` 数组；下游 `GET /v1/models` 只读账号入库的 `models_json`。细节以 `2026-08-15-provider-abstraction.md` 为准。

| 供应商 | 认证 | 默认 Base URL | 额度 | 探测 |
|--------|------|---------------|------|------|
| OpenCode Go | API Key | `https://opencode.ai/zen/go` | `GET {base}/v1/usage`，输出 progress + text | `GET .../v1/models` |
| Grok | xAI OAuth PKCE（对齐 sub2api） | `https://api.x.ai/v1` | Grok CLI billing，输出周额度 progress + text | `GET .../models`（带 access token） |
| DeepSeek | API Key | `https://api.deepseek.com` | `GET /user/balance`，输出 text | `GET /models` |

第一版不做官方 `XAI_API_KEY`。OAuth `client_id` 用环境变量 `XAI_OAUTH_CLIENT_ID`（可覆盖公开默认值）。回调用 `APP_BASE_URL`。

## 8. 管理界面

一套 React 响应式页面。基调：深色值机台（信号灯绿/琥珀/玫瑰），中文文案。字体：IBM Plex Sans + IBM Plex Mono。不用 Inter、不用紫渐变。

页面：

- 登录：用户名密码，JWT 存 `localStorage`
- 概览：上游账号数、异常账号、今日请求、今日失败
- 上游账号：列表 + 预设向导；Grok「去授权」；手动探测；刷新额度；启用/停用
- 下游 API Key：创建必选上游；随时显示完整 `sk-…`；停用/删除；不换绑
- 记录审计：按账号 / Key / 协议 / 状态 / 时间 / 模型筛选；详情是消息气泡，原始 JSON 默认可展开

手机：侧栏收成顶栏菜单；表格收成卡片；消息气泡全宽。

## 9. 技术栈

- 后端：FastAPI、Uvicorn、Pydantic、SQLAlchemy 2、SQLite、LiteLLM、cryptography、httpx
- 前端：Vite、React、TypeScript、React Router、TanStack Query、shadcn/ui、Tailwind、lucide-react
- 部署：`docker compose` 单容器 + 数据卷。前面要 HTTPS 自己挂反代。

环境变量：

| 变量 | 作用 |
|------|------|
| `APP_SECRET_KEY` | JWT + Fernet。丢了旧 Key 全部解不开 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 仅空库第一次种管理员 |
| `DATABASE_PATH` | 默认 `data/gateway.db` |
| `APP_BASE_URL` | Grok OAuth 回调根，如 `http://127.0.0.1:8000` |
| `REQUEST_TIMEOUT_SECONDS` | 默认 120 |
| `XAI_OAUTH_CLIENT_ID` | Grok OAuth 客户端，可覆盖默认 |

## 10. 测试范围

- 下游 Key：无效 / 停用 / 正常
- Key 绑死上游：请求打到指定账号
- OpenAI ↔ Anthropic 各一条 happy path（mock 上游）
- 流式结束后日志是完整回复
- 探测更新账号状态
- 管理接口未登录 401；登录后能建账号和 Key，并能读出完整 `sk-…`

Grok 真授权不进 CI，手工验收。

## 11. 第一版明确不做

用户注册、按模型自动调度、账号分组、定时探测、下游限额、多管理员 RBAC、会话树、多实例抢同一 SQLite、Prometheus、embeddings / 图像 / 音频。

## 12. 风险

- Grok OAuth 依赖 xAI 公开客户端与订阅策略，token 刷新或接口变更会导致该账号全部失败。
- LiteLLM 版本升级可能改变转换行为，锁定次要版本。
- 下游 Key 可逆：数据库与 `APP_SECRET_KEY` 不得一起备份。
- 库文件需限制权限，且不得与 `APP_SECRET_KEY` 一起备份。
- OpenCode Go 部分模型走 `/v1/responses`：入口 `/v1/responses` 必须接通，仅 chat completions 不够。
