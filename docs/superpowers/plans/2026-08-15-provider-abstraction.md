# 供应商抽象重构 Implementation Plan

> **For agentic workers:** 本计划已落地。回看或续做第四家时对照任务勾选和文件表，细节以 Spec 为准。

**Goal:** 把三个上游从硬编码 `PRESETS` + 各处 `if provider` 收成 Provider 基类。探测/拉模型/额度/凭证走子类；前端额度只渲染 `{label, type, value}` 数组。已实现功能迁完，加第四家不用改管理端额度 UI。

**Architecture:** `backend/app/providers/` 包：`Provider` / `OpenAICompatibleProvider` + 三家子类 + registry。`probe.py` / `quota.py` 只转发。Grok OAuth 协议仍在 `grok_oauth.py`，只通过 `prepare_credential` 挂钩。`GET /v1/models` 读账号 `models_json`，没有则空列表。

**Tech Stack:** 不新增依赖。后端 Python 类；前端一个 `QuotaItems` 组件。

**Spec:** `docs/superpowers/specs/2026-08-15-provider-abstraction.md`  
**落地提交:** `d1b2c7b`

---

## File map

```
backend/app/providers/__init__.py     registry
backend/app/providers/base.py         Provider、OpenAICompatibleProvider、QuotaItem / QuotaView
backend/app/providers/opencode_go.py
backend/app/providers/grok.py
backend/app/providers/deepseek.py
backend/app/providers.py              删除（避免与包抢导入）
backend/app/services/probe.py         转发 get_provider().probe / list_models
backend/app/services/quota.py         转发 get_provider().fetch_quota
backend/app/services/bridge.py        openai_api_base、prepare_credential
backend/app/services/proxy.py         list_models_payload 读 models_json
backend/app/routers/admin_accounts.py list_providers / get_provider
backend/app/routers/admin_dashboard.py missing_credential
backend/app/routers/oauth.py          auth_type == oauth
backend/app/routers/share.py          find_provider().label
frontend/src/lib/api.ts               QuotaItem / AccountQuota
frontend/src/pages/Accounts.tsx       QuotaItems；去授权看 auth_type
backend/tests/test_accounts.py
backend/tests/test_proxy_forward.py
backend/tests/test_keys.py
backend/tests/test_share.py
```

不改：`grok_oauth.py` 协议细节、协议转换、日志、reasoning、CC Switch 业务、创建账号表单的 Base URL。

---

### Task 1: 基类、registry、三家子类

**Files:**
- Create: `backend/app/providers/base.py`
- Create: `backend/app/providers/__init__.py`
- Create: `backend/app/providers/opencode_go.py`
- Create: `backend/app/providers/grok.py`
- Create: `backend/app/providers/deepseek.py`
- Delete: `backend/app/providers.py`

- [x] `Provider`：`id` / `label` / `auth_type` / `default_base_url` / `default_models` / `append_v1`
- [x] 默认实现：`openai_api_base`、`probe`、`list_models`、`fetch_quota` → `load_quota`、`prepare_credential`、`missing_credential`
- [x] `OpenAICompatibleProvider.append_v1 = True`；DeepSeek 不继承它
- [x] `QuotaView.to_dict()` 只出 `ok` / `items` / 可选 `message`
- [x] OpenCode Go：`load_quota` 打 `{base}/v1/usage`，窗口 → progress + text
- [x] DeepSeek：`load_quota` 打 `{base_url}/user/balance` → text
- [x] Grok：`load_quota` 打 CLI billing；`prepare_credential` refresh；`missing_credential` 看 oauth_token
- [x] `__init__.py` 按 opencode_go → grok → deepseek 注册

**验证：** 删掉旧模块后 `from app.providers import get_provider` 拿到包，不是残留的 `providers.py`。

---

### Task 2: 后端调用方改走 registry

**Files:**
- Modify: `backend/app/services/probe.py`
- Modify: `backend/app/services/quota.py`
- Modify: `backend/app/services/bridge.py`
- Modify: `backend/app/services/proxy.py`
- Modify: `backend/app/routers/admin_accounts.py`
- Modify: `backend/app/routers/admin_dashboard.py`
- Modify: `backend/app/routers/oauth.py`
- Modify: `backend/app/routers/share.py`

- [x] `probe_account` / `list_account_models` / `refresh_quota` 只调 `get_provider`
- [x] 创建账号：`auth_type`、默认 `base_url` 来自 Provider
- [x] `GET /api/admin/providers` 遍历 `list_providers()`
- [x] `call_chat` 用 `openai_api_base`；`prepare_credential` 不再写死 grok
- [x] `list_models_payload` 读 `parse_models_json(account.models_json)`，空则 `data: []`
- [x] Dashboard：`missing_credential` 替代 `provider == grok AND token is None`
- [x] OAuth start：`auth_type != oauth` → 400「该供应商不需要 OAuth」
- [x] 分享页中文名：`find_provider`

**验证：** 非 Grok 账号点授权 400；未拉模型的 Key 打 `GET /v1/models` 得到空列表。

---

### Task 3: 前端通用额度渲染

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Accounts.tsx`

- [x] `AccountQuota` 改为 `{ ok?, message?, items?: QuotaItem[] }`
- [x] 删除 `QuotaWindow` / `QuotaBalance`、`OPENCODE_WINDOW_META`、`quotaWindows` / `quotaBalances`、`BalancePanel` / `QuotaBars`
- [x] `QuotaItems`：`progress` 画条（0–100，≥90 红 / ≥70 琥珀），其余当 `text`
- [x] 「去授权」改为 `account.auth_type === 'oauth'`
- [x] 额度空态用 `quota.message`，不再按供应商写死文案
- [x] 去掉未知供应商把 raw JSON 塞进 `<pre>` 的逻辑

**验证：** `npx tsc -b` 通过。已有账号旧 `quota_json` 显示为空，点「刷新额度」后出现 items。

---

### Task 4: 测试跟着契约改

**Files:**
- Modify: `backend/tests/test_accounts.py`
- Modify: `backend/tests/test_proxy_forward.py`
- Modify: `backend/tests/test_keys.py`
- Modify: `backend/tests/test_share.py`

- [x] 额度断言改 `items`（DeepSeek 金额/构成、OpenCode 三段 progress+金额、Grok 周限制 percent）
- [x] `parse_grok_weekly_windows` 改为测 `grok_quota_items`
- [x] httpx mock 打到 `app.providers.base` / `deepseek` / `opencode_go` / `grok`
- [x] `GET /v1/models`：未入库为空；拉过模型后返回入库名单

**验证：**

```bash
cd backend && pytest -q
```

Expected: 69 passed

---

### Task 5: 提交

- [x] Commit: `refactor: 用供应商基类统一探测额度与前端展示`（`d1b2c7b`）

```bash
git add backend/app/providers.py backend/app/providers/ \
  backend/app/routers/admin_accounts.py backend/app/routers/admin_dashboard.py \
  backend/app/routers/oauth.py backend/app/routers/share.py \
  backend/app/services/bridge.py backend/app/services/probe.py \
  backend/app/services/proxy.py backend/app/services/quota.py \
  backend/tests/test_accounts.py backend/tests/test_keys.py \
  backend/tests/test_proxy_forward.py backend/tests/test_share.py \
  frontend/src/lib/api.ts frontend/src/pages/Accounts.tsx
git commit -m "refactor: 用供应商基类统一探测额度与前端展示"
```

---

## 上线注意

- 已有账号的额度条会空，管理员对每个号点一次「刷新额度」。
- 已发出的下游 Key，管理员对绑定账号点一次「获取模型」，否则客户端 `GET /v1/models` 是空的。
- 不要把 `需求.md` 或密钥文件打进提交。
