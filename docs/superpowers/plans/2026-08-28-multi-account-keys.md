# 一把 Key 关联多上游账号 Implementation Plan

> **For agentic workers:** 按任务勾选落地。细节以本文和 `CLAUDE.md` 为准。不要自动 commit。

**Goal:** 一把下游 API Key 可按优先级绑定多个上游账号（含网关代理）。请求按合并后的对外模型 id 选账号。同名模型先到先得保留裸名，冲突时后绑账号使用账号级自定义 `model_prefix` 变成 `prefix/raw`。已有 Key 自动迁成只绑原来那一个账号，客户端零改动。

**Architecture:** 新增 `api_key_accounts` 中间表（`sort_order` 即优先级）。`UpstreamAccount.model_prefix` 账号级可编辑。网关按 Key 的绑定顺序现场计算模型目录，不落库。命中对外 id 后把 `body.model` 改回上游原始 id 再转发。`ApiKey.account_id` 保留为冗余主账号（绑定第一项）。不做 failover / 轮询 / RPM。

**Tech Stack:** 现有 FastAPI + SQLAlchemy 2 + SQLite；前端 React 19 + Vite + Tailwind，不引入新 UI 框架。

**Spec 决策:** 见下方「已确认决策」。无单独 spec 文件。

---

## 已确认决策

- 选路：合并模型目录，按对外模型名选账号。不做 failover / 轮询 / RPM。
- 消歧：唯一模型保留裸名；只有重复时，非第一优先级的账号才加 `prefix/raw`。
- 优先级：绑定列表可排序，`sort_order` 越小越优先。排第一的账号占用冲突裸名；独有模型即使排后面也仍裸名。
- 前缀：账号级 `UpstreamAccount.model_prefix`，用户可自定义；默认由 `name` 生成 ASCII slug，空则 `acc-{id}`。规则 `[A-Za-z0-9][A-Za-z0-9_-]{0,31}`，禁止斜杠。
- 网关代理账号同样有前缀，在代理详情页编辑；`_sync_agent` 新建才写默认前缀，更新不得覆盖。
- 已有 Key：启动时把 `api_keys.account_id` 写入中间表 `sort_order=0`，对外行为不变。
- 多账号时，空 `models_json` 的账号不进目录。单账号（仅一个 active 绑定）空列表保持现网不校验、原样转发。
- 停用账号不进目录、不占裸名。单账号且该账号停用仍 403。
- 目录即时重算，不落库。`RequestLog` 仍记实际打到的 `account_id`。
- 兼容：KeyCreate/Update 仍接受单个 `account_id`；KeyOut 保留第一项的 `account_id` / `account_name` / `provider` / `account_source`。

## 不在范围

- 失败自动换号、负载均衡、RPM。
- Key 级覆盖前缀。
- 改 RequestLog 结构。
- Skills / Benchmark / 排行榜的选账号逻辑。
- 自动 Git commit。

## 对外模型目录

对一把 Key，按绑定 `sort_order` 升序遍历：

1. 跳过 `status != active`（不占裸名，不进目录）。
2. 多个 active 账号：只收录 `models_json` 非空的模型；空列表账号不参与。
3. 仅一个 active 账号：空 `models_json` 不校验，任意模型名原样转发到该账号。
4. 对每个上游原始 id `raw`：若 `raw` 尚未被占用则对外 id = `raw`；否则 `prefix/raw`；若仍冲突则 `prefix-account_id/raw`。
5. 请求进来时用对外 id 精确查找，转发前把 `body.model` 改回 `raw`。
6. 改前缀 / 改绑定顺序 / 刷新模型后，目录即时重算。调序可能把裸名从 A 转到 B，管理端需提示。

## File map

```
docs/superpowers/plans/2026-08-28-multi-account-keys.md
backend/app/models.py
backend/app/db.py
backend/app/services/key_models.py          新建
backend/app/schemas.py
backend/app/serializers.py
backend/app/deps.py
backend/app/routers/admin_keys.py
backend/app/routers/admin_accounts.py
backend/app/routers/proxy.py
backend/app/services/proxy.py
backend/app/routers/share.py
backend/app/routers/local_agent.py
backend/app/services/account_transfer.py
frontend/src/lib/api.ts
frontend/src/pages/Keys.tsx
frontend/src/pages/Accounts.tsx
frontend/src/pages/AgentDetail.tsx
backend/tests/test_key_models.py            新建
backend/tests/test_keys.py
backend/tests/test_accounts.py
backend/tests/test_proxy_auth.py
backend/tests/test_proxy_forward.py
backend/tests/test_share.py
backend/tests/test_local_agents_api.py
```

---

### Task 1: 数据层与目录算法

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py`
- Create: `backend/app/services/key_models.py`
- Create: `backend/tests/test_key_models.py`

- [ ] **Step 1: 模型**

`UpstreamAccount.model_prefix` VARCHAR(32) 可空。新表 `ApiKeyAccount`（`api_key_id`, `account_id`, `sort_order`），UNIQUE `(api_key_id, account_id)`，`api_key_id` ON DELETE CASCADE。`ApiKey.account_id` 改为可空冗余主账号。`account_links` cascade delete-orphan，按 `sort_order` 排序。保留 `ApiKey.account` 供第一项兼容字段。`UpstreamAccount.key_links` 供删账号检查。

- [ ] **Step 2: 迁移**

`_ensure_columns` 增加 `model_prefix`。建中间表（`create_all` 会建新表）。旧库若 `api_keys.account_id` 仍 NOT NULL，重建表改为可空。把已有 `api_keys.account_id` 插入中间表 `sort_order=0`。空前缀账号（含 agent）回填默认值。

- [ ] **Step 3: 目录纯函数**

`slug_model_prefix` / `default_model_prefix` / `normalize_model_prefix` / `ensure_account_prefix` / `bound_accounts` / `build_model_catalog` / `resolve_model` / `public_model_ids`。

- [ ] **Step 4: 单测**

裸名、冲突加前缀、双重冲突 `prefix-id`、停用跳过、空列表多账号排除、单账号空列表透传、中文名 `acc-{id}`、非法前缀。

---

### Task 2: Key / Account 管理 API

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/serializers.py`
- Modify: `backend/app/routers/admin_keys.py`
- Modify: `backend/app/routers/admin_accounts.py`
- Modify: `backend/app/services/account_transfer.py`
- Modify: `backend/tests/test_keys.py`
- Modify: `backend/tests/test_accounts.py`

- [ ] **Step 1: Schema / 序列化**

`KeyCreate` / `KeyUpdate` 增加有序 `account_ids`，兼容单个 `account_id`。`KeyOut` 增加 `account_ids` 与 `accounts`。`AccountCreate` / `Update` / `Out` 增加 `model_prefix`。

- [ ] **Step 2: 写中间表**

创建/更新 Key 时替换 join 行并同步 `account_id = account_ids[0]`。cc-switch 返回 public model ids。创建账号后 `ensure_account_prefix`。删账号查 `key_links`，detail 仍含 `API Key`。导入导出带前缀。

- [ ] **Step 3: 测试**

旧 `account_id` payload 仍能建 Key；`account_ids` 多绑与调序；创建回填 prefix；非法 prefix 422；导出导入保留 prefix；非主账号绑定也阻止删账号。

---

### Task 3: 代理选路与 Agent

**Files:**
- Modify: `backend/app/deps.py`
- Modify: `backend/app/routers/proxy.py`
- Modify: `backend/app/services/proxy.py`
- Modify: `backend/app/routers/share.py`
- Modify: `backend/app/routers/local_agent.py`
- Modify: `backend/tests/test_proxy_auth.py`
- Modify: `backend/tests/test_proxy_forward.py`
- Modify: `backend/tests/test_share.py`
- Modify: `backend/tests/test_local_agents_api.py`

- [ ] **Step 1: 选路**

`resolve_api_key` 加载 `account_links.account`。`_authenticate`：Key 有效且至少有一个 active 绑定。单账号停用保持现网 403 文案。`_validate_model` 查 catalog，命中后改写 `body.model` 为 raw。`GET /v1/models` 返回 public id。Share / CC Switch 用 public ids；`account_*` 仍取第一绑定。

- [ ] **Step 2: Agent**

`_sync_agent` 新建账号才写默认前缀。路由消失：先从 Key 解绑；无剩余账号则停用 Key（不删除），`account_id=NULL`。`_agent_to_out` 增加 `account_id`、`model_prefix`。

- [ ] **Step 3: 测试**

旧 Key `/v1/models` 仍裸名。两账号同名：裸名打第一，`prefix/raw` 打第二且上游收到 raw。调序后裸名改打新第一。Agent 重连不覆盖前缀。路由删除解绑并停用空 Key。`test_local_agents_api.py` 精确 JSON 断言改为含新字段或改为关键字段断言。单账号模型校验中文文案不变。

---

### Task 4: 前端

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Keys.tsx`
- Modify: `frontend/src/pages/Accounts.tsx`
- Modify: `frontend/src/pages/AgentDetail.tsx`

- [x] **Step 1: 类型**

`Account.model_prefix`；`ApiKeyItem.account_ids` / `accounts`；`createKey` 提交 `account_ids`；Agent 路由带 `account_id`、`model_prefix`。

- [x] **Step 2: Keys 有序绑定**

创建/编辑为有序列表：添加、上移/下移、移除，至少 1 个。第一行标注「优先 / 裸名」。保存提交 `account_ids`。调序提示裸名归属会变。列表卡片展示全部绑定；筛选匹配任一绑定账号。

- [x] **Step 3: 前缀 UI**

Accounts 表单增加模型前缀，空则后端默认。Agent 详情每条路由可保存前缀（`PATCH /api/admin/accounts/{id}`）。

---

### Task 5: 收口验证

- [ ] **Step 1: pytest**

```powershell
Set-Location backend
& C:\Users\xiaoxi\tools\python311\python.exe -m pytest -q tests/test_key_models.py tests/test_keys.py tests/test_accounts.py tests/test_proxy_auth.py tests/test_proxy_forward.py tests/test_share.py tests/test_local_agents_api.py
```

- [ ] **Step 2: 前端构建**

```powershell
Set-Location frontend
$env:Path = "C:\Users\xiaoxi\tools\node-v20.19.0-win-x64;$env:Path"
npm run build
```

Expected: pytest 全绿；`npm run build` 成功。不要 commit。
