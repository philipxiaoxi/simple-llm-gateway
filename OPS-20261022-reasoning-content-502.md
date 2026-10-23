# 运维现场报告：openai_responses 思考模型报 502（reasoning_content 未回传）

- 提交对象：后端开发
- 编写方：运维
- 日期：2026-10-22
- 环境：Docker 生产环境（容器 `llm-gateway`，镜像 `simple-llm-gateway-gateway`）
- 现象：`litellm.BadRequestError: OpenAIException - {"error":{"code":"server_error","message":"Upstream request failed: [invalid_request_error] The `reasoning_content` in the thinking mode must be passed back to the API. ..."}}`
- 当前状态：容器运行正常；**该错误是代码回归，尚未修复，思考类模型走 `openai_responses` 协议时会复发**
- 文档性质：运维侧现场证据、已采集材料、根因定位与修复建议
- 使用方式：本报告可直接转发给后端开发，无需补充上下文

**阅读指引：**

- 想快速了解结论：看第 1、2 节
- 想定位代码问题：看第 4 节
- 想安排修复：看第 5 节
- 想复核现场数据：看第 3、8 节

---

## 1. 结论摘要

这条报错**不是上游模型故障，也不是网络/额度问题**，而是网关在 `openai_responses` 协议下**丢失了 `reasoning_content` 回传**，导致需要"思考链回传"的 DeepSeek 系模型被上游拒绝。

直接原因（按链路）：

1. 客户端 `deepseek-harness` 以 **`openai_responses`** 协议、**流式**请求思考模型 `deepseek-v4.1-flash`（账号 `熙哥opencode套餐`，上游 `https://opencode.ai/zen/go/v1`）。
2. 网关在 `handle_chat` 里**确实**计算并调用了 `inject_reasoning_into_messages(messages, stored_reasoning)` 把 `reasoning_content` 注入到转换后的 `messages`（`backend/app/services/proxy.py:537`）。
3. 但 `openai_responses` 分支真正发往上游的**不是** `messages`，而是**原始 `body["input"]`**：
   `call_responses(account, sanitize_responses_input(body.get("input")), ...)`（`backend/app/services/proxy.py:562`、`:641`）→ `provider.responses()` → `litellm.aresponses(input=input_items, ...)`（`backend/app/providers/base.py:120-140`）。
4. 于是注入好的 `messages` **只被用于 token 估算**，从未发往上游——`reasoning_content` 回传在该协议下**完全失效**。
5. 上游（opencode Zen → DeepSeek）在思考模式 + 工具调用多轮场景下强制要求回传 `reasoning_content`，缺失即报 `invalid_request_error`，网关再把它包装成 **502** 返回。

这是 **2026-08-16 的提交 `1acbb4c`（用 litellm 官方 Responses 桥接替换自研转换器）引入的回归**：替换前 `openai_responses` 走的是 `messages` + `call_chat` + `responses_from_chat`，会带上注入的 `reasoning_content`；替换后改成直透原始 `input`，注入被旁路。

对照证据：同一个 `deepseek-harness` 客户端走 **`openai_chat`** 调 `deepseek-v4.1-flash` **成功 294 次**（reasoning_json 正常落库 20–65KB），走 **`openai_responses`** 仅 1 次且**失败**。非思考模型 `deepseek-flash` 走 `openai_responses` **成功 3065 次**（reasoning_json 为空），说明问题只在"思考模型 + responses 协议"组合上触发。

---

## 2. 影响与当前状态

- 首次/唯一一次报错（日志库）：**2026-10-22 01:26:08 UTC**，日志 id **#11118**。
- 影响面：
  - 客户端 `deepseek-harness/0.2.1-rc.2` 在 `openai_responses` 协议下使用思考类模型（如 `deepseek-v4.1-flash`、`deepseek-v4-pro` 等）时会 502 失败；
  - 走 `openai_chat` / `anthropic_messages` 的同类模型不受影响（注入生效）；
  - 非思考/不要求回传的模型（`deepseek-flash`）不受影响。
- 当前：
  - 近 10 天该会话仅失败 1 次，未造成大面积故障，但属于**确定性复现的代码缺陷**；
  - 上游错误被错误归类为 **502**（上游实际返回 `invalid_request_error` / 400 语义），会误导告警与重试策略；
  - 该条失败日志因会话续写合并，累计出 **86,318,140 prompt tokens / 4,338,855 ms(≈72 分钟)** 的异常台账数字，可能污染用量与图表统计。

---

## 3. 现场证据

> 数据来源：`/data/gateway.db`（容器内只读查询，`docker exec -i llm-gateway python`）。近 10 天 `request_logs` 共 2464 条，其中 `error` 24 条、`success` 2440 条。

### 3.1 目标错误记录（#11118）

```text
id             : 11118
created_at     : 2026-10-22 01:26:08.641785
account        : 19 熙哥opencode套餐  (provider=opencode_go, base_url=https://opencode.ai/zen/go/v1)
api_key        : 1 军哥专用
protocol       : openai_responses
model          : deepseek-v4.1-flash
stream         : true
status/http    : error / 502
session_key    : session-16b872ac-adef-4089-8d58-5d2ed1f13ba5
client         : deepseek-harness/0.2.1-rc.2 (+https://github.com/deepseek-ai/deepseek-harness)
client_ip      : 223.104.83.235 (中国 广东 佛山市)
reasoning_json : 1,549,037 bytes (354 条 by_tool_call_id)
usage          : prompt=86,318,140  completion=442,721  total=86,760,861   (会话续写累计值)
latency_ms     : 4,338,855
```

原始错误（265 字符）：

```text
litellm.BadRequestError: OpenAIException - {"error":{"code":"server_error","message":"Upstream request failed: [invalid_request_error] The `reasoning_content` in the thinking mode must be passed back to the API. (request_id: 7e03b999-7444-4adb-bafa-a8048228a970)"}}
```

### 3.2 会话现场

- 该 `session_key` 在库中**只有 1 条记录**（#11118），即本次请求即失败，没有同会话的成功续写。
- 该会话已存消息 **1000 条**（已触发滚动窗口上限 `MAX_LOG_MESSAGES=1000`），包含大量 `assistant(tool_calls)` / `tool` 交替，是一段长 agent 会话。
- 失败请求的 `reasoning_json` 已落库 **354 条**（按 `tool_call_id` 索引），说明**客户端本次请求确实带了 reasoning 内容**（见 4.4 的说明）。

### 3.3 统计对比（决定性问题定位）

| 客户端 | 协议 | 模型 | 结果 | 次数 | reasoning_json |
|---|---|---|---|---|---|
| deepseek-harness | `openai_responses` | deepseek-flash | success | 1305 | 空 |
| deepseek-harness | `openai_responses` | deepseek-flash | success(全账号19) | 3065 | 空 |
| deepseek-harness | `openai_chat` | deepseek-flash | success | 248 | 有 |
| deepseek-harness | `openai_chat` | deepseek-v4.1-flash | success | 93 | 有 |
| deepseek-harness | `openai_responses` | **deepseek-v4.1-flash** | **error** | **1** | 有(失败请求) |
| — | `openai_chat` | deepseek-v4.1-flash (账号19) | success | 294 | 有(20–65KB) |

结论：**失败仅出现在 `openai_responses` + 思考模型**；`openai_chat` 同模型大量成功。

### 3.4 近期错误全景（近 10 天 24 条 error）

绝大多数是**上游业务性错误**，与本次无关，例如：

- Grok 额度/订阅：`personal-team-blocked:spending-limit` (403)、`余额不足` (403)；
- `deepseek-flash` 模型名不存在：`Error code: 400 - {'model': 'deepseek-flash'}`；
- 上游限流/超时：`RateLimitError`、`MidStreamFallbackError/APIConnectionError`。

本次 #11118 属其中**唯一的 `reasoning_content` 协议错误**。

---

## 4. 根因机制

### 4.1 请求链路（当前代码）

```text
客户端(openai_responses, stream)
  → routers/proxy → services/proxy.handle_chat
      protocol == "openai_responses":
        messages = input_to_messages(body)                    # proxy.py:504
        extra    = responses_extra_passthrough(body)
      inbound_reasoning = reasoning_map_from_messages(messages)
      stored_reasoning  = merge(load_reasoning_map(...), inbound_reasoning)
      inject_reasoning_into_messages(messages, stored_reasoning)   # proxy.py:537  ← 只注入到 messages
      → _stream_response(messages, responses_input=sanitize_responses_input(body["input"]), ...)
          protocol == "openai_responses":
            result = call_responses(account, responses_input, ...)  # proxy.py:641 ← 用的是原始 input
                      → provider.responses() → litellm.aresponses(input=input_items, ...)  # base.py:120
```

关键点：**注入后的 `messages` 没有进入 responses 分支的请求体**。`messages` 在 responses 分支里只用于 `estimate_usage(model, messages, ...)`。

### 4.2 `reasoning_content` 注入只覆盖了 chat / anthropic 路径

- 注入函数：`backend/app/services/reasoning.py:202 inject_reasoning_into_messages()`，对带 `tool_calls` 的 assistant 消息写入 `reasoning_content`（无命中时填 `PLACEHOLDER_REASONING=" "`）。
- 该函数最初由提交 `5b7845e`（"fix: 回传 reasoning_content 避免思考模式报错"，2026-08-15）引入，目的就是解决 **DeepSeek 带工具多轮必须回传 reasoning_content** 的问题。
- 该提交同时**弃用了 `litellm.anthropic_messages`**，改为自研 Anthropic→OpenAI 转换 + `call_chat`，因此注入在 chat/anthropic 路径生效。

### 4.3 回归点：提交 `1acbb4c` 把 responses 路径改成直透原始 input

提交 `1acbb4c`（"feat: 用 litellm 官方 Responses 桥接替换自研转换器"，2026-08-16）的 diff 显示：

```diff
-        extra = _passthrough_keys(body)
+        extra = responses_extra_passthrough(body)
...
-        result = await call_chat(account, messages, model, False, extra, credential)
+        if protocol == "openai_responses":
+            result = await call_responses(
+                account, sanitize_responses_input(body.get("input")), model, False, extra, credential
+            )
+        else:
+            result = await call_chat(account, messages, model, False, extra, credential)
...
-        payload = responses_from_chat(openai_payload, model)
+        payload = responses_payload_to_dict(result)
```

替换前：`openai_responses` 通过 `messages`（已注入 reasoning）走 `call_chat`，再用 `responses_from_chat` 转回 Responses 结构 → **注入生效**。
替换后：`openai_responses` 直接透传 `body["input"]` 给 `litellm.aresponses` → **注入被旁路**，这就是本次报错的结构性原因。

> 相关提交：
> - `5b7845e`（2026-08-15）引入 reasoning_content 回传修复
> - `1acbb4c`（2026-08-16）替换为 litellm Responses 桥接，引入本次回归

### 4.4 客户端已带 reasoning，但上游仍拒绝（需开发确认）

失败请求的 `reasoning_json` 有 **354 条**，说明 `deepseek-harness` 本次请求**已带 reasoning 项**（由 `input_to_messages` → `reasoning_map_from_messages` 提取）。但上游仍返回 `reasoning_content ... must be passed back`。两种可能：

1. **Responses 格式的 reasoning 项未被上游/litellm 正确映射**为 DeepSeek 所需的 `reasoning_content`（即 Zen 侧适配问题）；或
2. **客户端只在部分回合带了 reasoning**，而报错发生在缺失的那一回合。

无论哪种，运维侧建议以**网关自己保证回传**为准，不要依赖客户端：走 `openai_chat` 的 294 次成功已证明"网关注入 `reasoning_content`"这条路是可靠的。

### 4.5 附带发现的台账异常

1. **错误归类错误**：上游是 `invalid_request_error`（400 语义的客户端/协议错误），网关 `_fail()` 一律按 502/504 返回（`proxy.py:818`），会误导告警、重试与用户排查。
2. **Token/延迟异常膨胀**：`save_log` 的续写合并（`find_continuation_log`）会把整段会话累计进同一条 log，失败记录出现 `86M tokens / 72min`。这类记录会污染"今日用量/图表/分享页"统计，也需要评估。

---

## 5. 请开发者确认/实现的事项

### Phase 1：恢复 reasoning 回传（核心修复，必须）

- 针对 `openai_responses` 协议：**在发往上游前把 `stored_reasoning` 注入回请求**。可选实现：
  - **方案 A（推荐，风险最低）**：`openai_responses` 也走 `messages` + `call_chat`（恢复 `1acbb4c` 之前的行为），保留"已注入 reasoning 的 messages"，再用 Responses 转换器把结果转回 Responses 格式（可参考被删的 `responses_from_chat` 或现有 Responses 字段映射）。
  - **方案 B**：仍用 `litellm.aresponses`，但在 `sanitize_responses_input` 之后、`call_responses` 之前，把 `stored_reasoning` 按 `function_call.call_id` 注入成上游可接受的 `reasoning` 项 / `reasoning_content`（需与 opencode Zen 实际接受的字段对齐）。
- 无论 A/B：确认 `inject_reasoning_into_messages` 在 responses 路径不再是"只用于 token 估算的死路径"（`proxy.py:537` 与 `:562/:641` 的割裂）。

### Phase 2：不要依赖客户端

- 该缺陷的本质是"客户端可能不带 / 上游不认 Responses 格式的 reasoning"。请以**网关落库的 `reasoning_json`** 为准做回传，并对缺失项按现有策略填 `PLACEHOLDER_REASONING`。

### Phase 3：错误归类与重试

- 区分上游 4xx 与 5xx：`invalid_request_error` / `BadRequestError` 不应返回 502；保留上游 `code` / `request_id` 便于排查。
- 评估是否对 `reasoning_content` 类错误做"补齐后重试一次"。

### Phase 4：台账与统计

- 评估会话续写合并导致的 token/latency 累计异常是否需要在失败记录上区分展示。

---

## 6. 运维可配合事项

- 可按需导出指定 `session_key` / `account_id` / 时间窗的完整 `request_logs` 与 `request_log_messages`。
- 可开临时调试：`litellm._turn_on_debug()`（仅在指定账号、短窗口内），抓取发往 opencode Zen 的真实请求体，以确认 reasoning 项的字段形态（用于方案 B 对齐）。
- 可提供一个最小复现：固定账号 19 + `deepseek-v4.1-flash` + `openai_responses` + `stream=true`，由开发提供请求样例，运维回放并把上游原始响应全部留存。

---

## 7. 总体验收标准

1. `openai_responses` + `deepseek-v4.1-flash`（含工具多轮、思考模式、stream）连续请求成功，无 `reasoning_content ... must be passed back`。
2. 复测样本同时覆盖：`openai_chat` / `anthropic_messages` / `openai_responses` 三种协议，且 `deepseek-flash`（非思考）不回退。
3. 上游 4xx 不再被网关记为 502。
4. 失败请求不再出现 `86M tokens / 72min` 一类异常累计。

---

## 8. 附：本次采集命令（可复现）

```bash
# 目标错误记录
docker exec -i llm-gateway python - <<'PY'
import sqlite3
con=sqlite3.connect("/data/gateway.db", timeout=30); con.row_factory=sqlite3.Row
print(dict(con.execute("SELECT id,created_at,account_id,account_name,api_key_id,api_key_name,protocol,model,stream,status,http_status,session_key,user_agent,length(reasoning_json) rjl FROM request_logs WHERE id=11118").fetchone()))
print(con.execute("SELECT error_message FROM request_logs WHERE id=11118").fetchone()[0])
PY

# 协议/模型维度成功率对比
docker exec -i llm-gateway python - <<'PY'
import sqlite3
con=sqlite3.connect("/data/gateway.db", timeout=30); con.row_factory=sqlite3.Row
for r in con.execute("SELECT account_id,model,protocol,status,COUNT(*) c FROM request_logs WHERE model LIKE 'deepseek%' GROUP BY account_id,model,protocol,status ORDER BY c DESC"):
    print(dict(r))
PY
```

代码定位速查：

```text
backend/app/services/proxy.py:504    messages = input_to_messages(body)          # responses 转换
backend/app/services/proxy.py:537    inject_reasoning_into_messages(messages,...) # 注入只进 messages
backend/app/services/proxy.py:562    call_responses(account, ..., body["input"])  # responses 直透原始 input
backend/app/services/proxy.py:641    call_responses(account, responses_input,...) # 流式同样直透
backend/app/providers/base.py:120    litellm.aresponses(input=input_items, ...)   # 发往上游
backend/app/services/reasoning.py:202 inject_reasoning_into_messages()            # 注入实现
```
