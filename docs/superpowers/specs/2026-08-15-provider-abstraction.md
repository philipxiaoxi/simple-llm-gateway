# 供应商抽象重构

日期：2026-08-15  
状态：已落地（`d1b2c7b`）  
关联：`docs/superpowers/specs/2026-08-14-llm-gateway-design.md` §7  
计划：`docs/superpowers/plans/2026-08-15-provider-abstraction.md`

第一版把三个上游写进 `PRESETS` 字典，探测/额度/OAuth/前端展示各自 `if provider == ...`。加第四家要同时改后端分叉和前端组件。本次把供应商差异收进基类，额度改成前端可通用渲染的数组。

## 1. 目标

- 供应商差异只出现在子类里。通用行为（探测 `/models`、解析模型 id、缺 `/v1` 就补）放基类。
- 前端额度不再按供应商名分支，只渲染 `{label, type, value}` 数组。
- 加第四家 = 新写一个子类并 `register_provider`，管理端额度区不用改。
- 已实现的三家能力都迁过去：探测、拉模型、额度、Grok OAuth refresh、Dashboard 缺凭证统计。

## 2. 拍板记录

对话里确认过、实现按此执行：

| 议题 | 决定 |
|------|------|
| 额度数据结构 | `{label, type: 'text' \| 'progress', value}`。复杂信息先全部落成 `text`，以后要新展示形态再加 `type`，不加 `hint` / `windows` / `balances`。 |
| `progress` 的 `value` | 0–100 的数字。金额、重置时间、赠送/充值、余额不足都是旁边的 `text` 行。 |
| `GET /v1/models` | 只读账号上「获取模型」写入的 `models_json`。没有就返回空列表，不再回落预设名单。 |
| 旧 `quota_json` | 不兼容。库里若还是 `windows` / `balances`，前端当没有额度，等管理员再点「刷新额度」。 |
| OAuth | 不做成通用授权框架。Grok 协议留在 `grok_oauth.py`，供应商只暴露 `auth_type` 和 `prepare_credential`。 |
| 注册方式 | 代码里的类 + registry，不是数据库或配置文件。 |
| `default_models` | 仍留在类上，给 `GET /api/admin/providers` 看。下游 `GET /v1/models` 不用它。 |
| 创建账号改 `base_url` | 支持。表单预填预设，可改；已有账号卡片上也能改并保存。空/空白回落到预设。 |

## 3. 结构

```
backend/app/providers/
  __init__.py          registry：register / find / get / list
  base.py              Provider、OpenAICompatibleProvider、QuotaItem、QuotaView
  opencode_go.py
  grok.py
  deepseek.py
```

`backend/app/providers.py` 已删除，避免和包同名抢导入。

```
GET /api/admin/providers  ──► list_providers()
创建账号 / 转发 / 探测     ──► get_provider(account.provider)
                     │
                     ├─ openai_api_base()
                     ├─ probe() / list_models()
                     ├─ fetch_quota() → load_quota()
                     ├─ prepare_credential()
                     └─ missing_credential()
```

`services/probe.py`、`services/quota.py` 只做转发，不再写供应商分叉。

## 4. 基类

`Provider` 类属性：

| 属性 | 含义 |
|------|------|
| `id` | 写入 `upstream_accounts.provider` 的稳定标识 |
| `label` | 中文名，管理端 / 分享页用 |
| `auth_type` | `api_key` 或 `oauth`。创建账号时抄进账号行，前端「去授权」看这个，不看供应商名 |
| `default_base_url` | 创建时未传 `base_url` 就用这个 |
| `default_models` | 预设名单，只给管理 API 的供应商列表 |
| `append_v1` | 默认 `False`。`OpenAICompatibleProvider` 设为 `True` |

方法：

| 方法 | 默认行为 | 谁重写 |
|------|----------|--------|
| `openai_api_base` | 按 `append_v1` 决定要不要补 `/v1` | 一般不用 |
| `probe` | 轮询 `model_candidate_urls`，写 `last_probe_*` | 一般不用 |
| `list_models` | 同上，解析 id 写入 `models_json` | 一般不用 |
| `fetch_quota` | 没凭证则 `{ok:false, message}`；有则 `load_quota` 后写入 `quota_json` | 一般不用 |
| `load_quota` | 试 `/usage`、`/billing`，成功则一条 `text`；否则「不支持查询余额」 | 三家都重写 |
| `prepare_credential` | `require_upstream_credential` | Grok：refresh |
| `missing_credential` | `False` | Grok：没有 `oauth_token` |

`OpenAICompatibleProvider` 只改 `append_v1 = True`。OpenCode Go、Grok 继承它；DeepSeek 直接继承 `Provider`，避免把 `https://api.deepseek.com` 错补成 `/v1`。

探测候选 URL（顺序保留）：规范化后的 `{base}/models`、原始 `{base_url}/models`、原始 `{base_url}/v1/models`。

## 5. 额度约定

写入 `upstream_accounts.quota_json`，同时作为 `POST /api/admin/accounts/{id}/quota` 的响应：

```json
{
  "ok": true,
  "message": "可选，失败或空态文案",
  "items": [
    { "label": "周限制", "type": "progress", "value": 25.0 },
    { "label": "周限制", "type": "text", "value": "重置时间：2026-08-16T09:32:10.577883+00:00" }
  ]
}
```

规则：

- `type` 目前只有 `text`、`progress`。前端不认识的 type 不要发明分支，先当 `text` 或忽略。
- 要新展示（大号金额、徽标、caption）就加新 `type`，不要按 `provider` 画。
- 不再输出 `windows`、`balances`、`supported`、`available`、`raw`、`provider`。
- 旧 JSON 不转。前端只认 `items`；没有就显示 `message` 或「还没有额度，点「刷新额度」拉取。」

三家怎么填：

| 供应商 | items |
|--------|--------|
| DeepSeek | `text` 币种 → `$9.90`；`text` 构成 → `赠送 $0.00 · 充值 $0.00`；`is_available === false` 再加一条状态 |
| OpenCode Go | 每个窗口一条 `progress`（percent）+ 一条 `text`（`$used / $limit · 重置时间`，status 非 ok 时拼上） |
| Grok | 一条 `progress`（周额度 percent）+ 一条 `text`（重置时间） |

OpenCode 窗口限额仍写在子类里：5 小时 $12、周 $30、月 $60。

## 6. 三个实现

| `id` | 类 | 认证 | 默认 Base | 额度入口 |
|------|-----|------|-----------|----------|
| `opencode_go` | `OpenCodeGoProvider` | api_key | `https://opencode.ai/zen/go` | `GET {base}/v1/usage` |
| `grok` | `GrokProvider` | oauth | `https://api.x.ai/v1` | `GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`（grok-cli 请求头） |
| `deepseek` | `DeepSeekProvider` | api_key | `https://api.deepseek.com` | `GET {base_url}/user/balance` |

Grok 额外：

- `prepare_credential` → `refresh_if_needed`
- `missing_credential` → `oauth_token is None`（Dashboard 异常账号用这个，不再写死 `provider == grok`）
- OAuth 起跳看 `auth_type == oauth`，文案改为「该供应商不需要 OAuth」

第一版仍不做官方 `XAI_API_KEY`。

## 7. 调用方

| 位置 | 用法 |
|------|------|
| `routers/admin_accounts.py` | 列表用 `list_providers()`；创建用 `get_provider` 填 `auth_type` / 默认 `base_url` |
| `routers/oauth.py` | `auth_type != oauth` 则 400 |
| `routers/admin_dashboard.py` | 遍历账号，`missing_credential` + 探测失败 = 异常数 |
| `routers/share.py` | `find_provider` 取 `label` |
| `services/bridge.py` | `openai_api_base`、`prepare_credential` |
| `services/proxy.py` | `GET /v1/models` 读 `parse_models_json(account.models_json)` |
| `services/probe.py` / `quota.py` | 转给对应 Provider |
| `frontend/src/pages/Accounts.tsx` | `QuotaItems`；「去授权」看 `auth_type` |

协议转换、日志、reasoning、CC Switch 业务未改。分享页只改了中文名来源。

## 8. 加第四家

1. 新建 `backend/app/providers/<id>.py`，继承 `Provider` 或 `OpenAICompatibleProvider`。
2. 填 `id` / `label` / `auth_type` / `default_base_url` / `default_models`。
3. 额度接口和现有三家不同就重写 `load_quota`，返回 `QuotaView`。
4. 需要 refresh 或特殊取凭证就重写 `prepare_credential`。
5. 缺凭证要算进 Dashboard 异常，就重写 `missing_credential`。
6. 在 `__init__.py` 里 `register_provider(...)`。
7. 管理端额度区不用改。OAuth 若不是 Grok 那套 PKCE，还要另写授权流程——基类不负责。

## 9. 行为变化（相对 2026-08-14 设计）

这些是有意改的，不是疏漏：

1. **下游模型列表**  
   旧：`GET /v1/models` 返回代码里写死的预设。  
   新：只返回该账号「获取模型」入库的列表；没拉过就是 `data: []`。已经发出去的 Key，管理员需要先点一次「获取模型」，客户端才能列出模型。

2. **额度 JSON**  
   旧：`windows` / `balances` / `raw`，前端按供应商画。  
   新：只有 `ok` / `message` / `items`。上线后旧数据空白，再刷新一次。

3. **额度布局**  
   DeepSeek 不再是大号金额 +「余额不足」徽标；OpenCode / Grok 的金额和重置时间变成进度条下的普通文字。信息还在。

## 10. 明确不做

- 供应商配置表、热加载、插件目录
- 通用 OAuth 框架（callback、PKCE、loopback 仍是 Grok 专用）
- 定时探测、账号池、按模型调度
- 创建账号表单里编辑 Base URL
- 把 `default_models` 当下游兜底名单

## 11. 测试

`backend/tests/test_accounts.py`、`test_proxy_forward.py` 已改成断言 `items`，以及「未拉模型时 `/v1/models` 为空、拉过之后返回入库名单」。httpx mock 打在对应子类或 `app.providers.base`。
