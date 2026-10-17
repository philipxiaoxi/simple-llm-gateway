# 性能基准与前后对比（2026-09-10）

正式服数据量：`request_logs` 6156 条、`content_audit_findings` 16489 条、SQLite、日常 10 人以内 / 峰值 5 并发。
本地库只有百余条，测不出真实耗时，因此用 `scripts/perf_seed_scale.py` 造两份同构数据：

| 数据集 | 规模 | 用途 |
| --- | --- | --- |
| `data/perf-scale.db` | 6156 日志 / 16489 命中 / 4.3 万消息 / 400 测速结果 | 复现正式服今天的规模 |
| `data/perf-growth.db` | 5 万日志 / 6 万命中 / 20 万消息 / 2 万测速结果 | 看数据继续增长后的表现 |

## 怎么复现

```bash
# 造数（--force 覆盖）
.venv/bin/python scripts/perf_seed_scale.py --force
.venv/bin/python scripts/perf_seed_scale.py --db data/perf-growth.db \
  --logs 50000 --messages 200000 --big-logs 20 --findings 60000 \
  --bench-runs 100 --bench-results 20000 --force

# 起一个只读这份数据的后端
DATABASE_PATH=data/perf-scale.db FRONTEND_DIST=frontend/dist \
  .venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001

# 采样 + 与历史结果对比
python3 scripts/perf_bench.py --label scale-final --base-url http://127.0.0.1:8001
python3 scripts/perf_bench.py --label x --base-url http://127.0.0.1:8001 \
  --compare docs/perf/scale-before-260910.json
```

`--concurrency N` 模拟多人同时打开页面。

## 首屏资源：修复前 → 修复后

| 项目 | 修复前 | 修复后 | 说明 |
| --- | --- | --- | --- |
| 入口 JS | 583 KB（单包，未压缩） | 39 KB + vendor 353 KB | 18 个页面全部静态导入 → 路由级懒加载 + vendor 拆分 |
| 首屏 JS 传输（brotli） | 583 KB | 约 103 KB | 构建期生成 `.br`，后端直接发送 |
| CSS | 64.8 KB | 66.0 KB（brotli 10.4 KB） | 仅增加 3 条 `@font-face` |
| 字体 | `fonts.googleapis.com` 阻塞样式表 + 2 个 preconnect | 自托管 3 个 woff2（75 KB，同源） | 大陆访问 Google Fonts 不可靠 |
| SW 首次预缓存 | 990 KiB / 15 条（含 361 KB 图标） | 726 KiB / 28 条（无图标） | 图标由浏览器按需请求 |
| `/assets/*` 缓存头 | 无 `Cache-Control`（每次回源校验） | `public, max-age=31536000, immutable` | 文件名带内容哈希 |
| 图标缓存头 | 无 | `public, max-age=86400` | 非哈希文件，保持短缓存 |

## 接口响应：压缩后的传输量

| 接口 | 原始 | gzip 传输 | 节省 |
| --- | --- | --- | --- |
| `/api/admin/leaderboard` | 70.5 KB | 2.9 KB | 96% |
| `/api/admin/accounts` | 2.6 KB | 0.4 KB | 84% |
| `/api/admin/logs?page=1` | 8.6 KB | 1.1 KB | 87% |
| `/api/admin/content-audit/findings` | 6.9 KB | 1.0 KB | 86% |

SSE（`text/event-stream`）不压缩、不缓冲；客户端未声明 `Accept-Encoding` 时不压缩。

## 接口耗时

### 正式服规模（今天的数据）

修复前后都在同一份数据上测，全部 ≤ 7 ms，`docs/perf/scale-before-260910.json` vs `scale-final-260910.json`：

| 接口 | 修复前 | 修复后 |
| --- | --- | --- |
| `/api/admin/dashboard` | 4.5 ms | 4.6 ms |
| `/api/admin/leaderboard` | 3.2 ms | 3.3 ms |
| `/api/admin/logs` | 1.4 ms | 1.4 ms |
| `/api/admin/content-audit/summary` | 5.1 ms | 6.3 ms |
| `/api/admin/jobs` | 5.7 ms | 6.2 ms |

结论：**今天的数据量下后端不是瓶颈**，慢在传输量与首屏资源；下面的收益要在数据增长后才显现。

### 增长规模（5 万日志 / 2 万测速结果）

`growth-before-260910.json` vs `growth-after-260910.json`：

| 接口 | 修复前 | 修复后 | 说明 |
| --- | --- | --- | --- |
| `/api/admin/dashboard` | 164.8 ms | 32 ms | 排行榜不再全表扫描 `benchmark_results` |
| `/api/admin/leaderboard` | 154.6 ms | 25–65 ms | 只回看最近 30 次测速 |
| `/api/admin/benchmark/history` | 19.6 ms（p95 55.6） | 1.7 ms | 计数改为 SQL 聚合，不再逐行读 results |
| `/api/admin/content-audit/summary` | 31.1 ms | 33 ms | 本次未优化，见后续项 |
| `/api/admin/jobs` | 32.7 ms | 34 ms | 本次未优化，见后续项 |

### 5 并发（正式服规模）

`scale-concurrent-before-260910.json` vs `scale-concurrent-after-260910.json`：

| 接口 | 修复前 | 修复后 |
| --- | --- | --- |
| `/api/admin/benchmark/history` | 34.9 ms | 3.9 ms |
| `/api/admin/logs` | 8.4 ms | 8.3 ms |
| `/api/admin/leaderboard` | 14.4 ms | 15.0 ms |
| `/api/admin/dashboard` | 42.7 ms | 42.2 ms |

并发下 dashboard/leaderboard 的放大来自 GIL 绑定的 Python 计算；曾试过 `asyncio.to_thread`，
但在正式服规模下反而更慢（35 ms vs 15 ms），因此保留同步实现，只做查询范围收窄。

## 已排除的假设

- **SQLite 写锁**：持续写入（20 事务/秒）期间管理接口读延迟中位仍是 2.3 ms（WAL 生效）。
- **深分页**：50 万行上 `OFFSET 99,980` 仅 1.19 ms。
- **序列化 / 加密 / 连接池**：0.1–0.7 ms 量级。

## 后续可做

- `/api/admin/accounts` 默认只返回模型数量，展开时再取明细。
- `/api/admin/skills` 延迟加载 `skill_md`/`analysis_json`，过滤下推到 SQL。
- 仪表盘的 12 个标量统计合并成一次聚合；`request_logs` 的全表 `SUM(total_tokens)` 在 5 万行时已是主要成本。
- 内容审计 `/summary`（每 2 秒被轮询）的 `max(seq) GROUP BY log_id` 可以改成增量维护。

## 请求详情页改造（2026-10）

详情页消息区原先一次性拉取并渲染单会话全部消息（最多 1000 条、数 MB JSON），是详情页卡顿的主因。现已改为：

| 能力 | 接口 | 说明 |
| --- | --- | --- |
| 消息分页 | `/api/admin/logs/{id}/messages` | 默认 50/页、上限 200；列表只返回 `content_preview`/`content_truncated`/`content_length` 与工具调用摘要，不再传全文 |
| 轮次范围全文 | 同上，`from_seq`+`to_seq`+`detail=true` | 轮次预览弹窗按范围取整轮全文 |
| 单条全文 | `/api/admin/logs/{id}/messages/{seq}` | 按需取单条完整正文 |
| 轮次轮廓 | `/api/admin/logs/{id}/turns` | 只读 seq/role 划分轮次边界（绝对轮次号），不读正文 |
| 思考按需加载 | `/api/admin/logs/{id}/reasoning/{call_id}` | 详情只返回思考摘要（长度+预览），展开时按 call_id 取全文 |
| 全量导出 | `/api/admin/logs/{id}/export` | 「内容」Tab 下载完整 JSON（消息+思考），替代内联渲染 |
| 模型解析链路 | 详情 `model_chain` 字段 | 请求时记录客户端请求名/别名/公开模型名到 `request_logs`（新增 3 个可空列，历史数据不回填，直接展示上游模型名） |
| 截断提示 | 详情 `storage_truncated` | 消息数达到单会话保留上限（1000）时提示：滚动窗口已生效，较早的普通消息被清理，保留头部系统消息 + 最新消息 |

配套调整：`perf_bench.py` 的 `API_PATHS` 已补详情页接口基准；内容审计 summary 的轮询间隔 2s→5s，扫描运行期间 `remaining` 由扫描线程增量维护，不再每轮跑反连接大查询。

## 单会话消息存储：滚动窗口（2026-10）

存储策略由「超上限后丢弃新消息」改为**滚动窗口**：单会话最多保留 `MAX_LOG_MESSAGES`（1000）条，超限时从最旧的普通消息开始清理，**头部连续系统消息 + 最新 N 条**始终保留。要点：

- seq 单调递增、删除后留空洞，分页/轮次轮廓/审计增量扫描均不受影响；
- 会话续写去重改为「尾部末条对齐」：正常续写只读取**最后一条已存消息**判断（`inbound` 末条=重发、倒数第二条=已存末条=正常续写），头部前缀匹配仅作为兜底在罕见场景按需加载。已滚动清理的中间消息不会被客户端重发历史反复补录；
- 审计的超长会话跳过阈值 `MAX_AUDIT_MESSAGES` 改为按**实际存留条数**判断（原来按 `max(seq)` 判断，滚动窗口下会失效）；
- 历史数据不迁移：已存会话在后续续写时自然进入滚动窗口；存量旧会话超限的部分保持原状。

### 转发热路径优化（`save_log` / `append_log_messages`）

请求收尾（finalize）在非流式请求中位于用户等待路径、流式请求中位于最后一个 token 之后，本次把它的成本从「随历史线性增长」降为常数级：

| 优化项 | 改造前 | 改造后 |
| --- | --- | --- |
| 续写去重 | 每次加载并解码头部 `len(inbound)` 条消息 + 逐条指纹比对 | 只读最后 1 条已存消息判重，头部仅兜底时加载 |
| finalize 读路径耗时（1000 条历史，2KB/条） | 约 11.8 ms | 约 0.13 ms（与历史长度无关） |
| finalize 读路径耗时（50 条历史） | 约 0.78 ms | 约 0.11 ms |
| append 统计查询 | `max(seq)` + `count(*)` 两次查询 + 逐行 ORM 删除 | 一次 `max(seq), count(*)` 合并查询 + 单条 `DELETE ... WHERE id IN (SELECT ... LIMIT n)` |
| `api_keys.last_used_at` | 每请求 UPDATE 一行 | 按分钟节流，降低 SQLite 写放大 |

基准脚本口径：SQLite 单表、会话内消息注入 `decode` 后模拟客户端重发完整历史，取 15 次中位数；滚动稳态 append 含 `commit`（0.48ms → 0.36ms）。
