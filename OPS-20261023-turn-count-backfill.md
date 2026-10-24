# 运维执行单：回填日志调用次数（turn_count）

- 面向对象：运维
- 日期：2026-10-23
- 环境：Docker 生产（容器 `llm-gateway`，数据卷 `./data:/data`，库 `/data/gateway.db`）
- 预计耗时：9G 库全量约 10–40 分钟（取决于合并会话数量），可分批、可中断、可续跑
- 影响面：只写 `request_logs` 两列；不动 tokens、不动 latency_ms、不删任何数据

---

## 1. 这次要做什么

一条 `request_logs` 记录是一个**会话**（同账号 / Key / 协议 / session，当天续写合并）。
所以 `latency_ms` 是会话累计耗时、`total_tokens` 是累加值，之前直接拿去算「请求数/平均耗时」，
才出现「86M tokens、72 分钟挂在一条 502 上」这种台账异常。

新版本已把口径拆开：

| 列 | 含义 | 来源 |
|---|---|---|
| `latency_ms` | 会话累计耗时（不变） | 续写时累加 |
| `last_latency_ms` | **最近一次调用**耗时（延迟统计用） | 每次调用覆盖 |
| `turn_count` | 该会话聚合的**调用次数** | 新建 1，续写 +1 |

仪表盘的「调用数」= `sum(turn_count)`，延迟均值/P95 用 `last_latency_ms`。

**新数据在发版后自动是精确的；本次执行只回填历史行。**
回填前历史合并行 `turn_count` 为 NULL，统计按 1 次计 —— 这就是「计数还没变化」的原因。

---

## 2. 执行前检查（4 条，全部通过才继续）

在**仓库根目录**执行，先设置变量：

```bash
CONTAINER=llm-gateway     # 用 docker ps 确认实际容器名
```

### 2.1 镜像与代码是新的

```bash
docker exec -i "$CONTAINER" grep -c last_latency_ms /app/app/services/proxy.py
```

- 输出 `1`（或大于 0）→ 代码已更新，继续。
- 输出 `0` → 镜像还是旧的，先更新镜像再回来：
  ```bash
  docker compose build && docker compose up -d
  ```

### 2.2 新列已存在

```bash
docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
con = sqlite3.connect("/data/gateway.db", timeout=30)
cols = [r[1] for r in con.execute("PRAGMA table_info(request_logs)")]
print("turn_count:", "turn_count" in cols, "last_latency_ms:", "last_latency_ms" in cols)
PY
```

两列都必须是 `True`（不对说明 2.1 没做对）。

### 2.3 看清待回填规模

```bash
docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
con = sqlite3.connect("/data/gateway.db", timeout=30)
print(con.execute("""
  SELECT count(*) AS total_logs,
         sum(CASE WHEN turn_count IS NULL THEN 1 ELSE 0 END) AS pending,
         sum(CASE WHEN turn_count IS NULL AND updated_at > created_at THEN 1 ELSE 0 END) AS pending_merged
  FROM request_logs
""").fetchone())
PY
```

记录 `pending`（待回填行数）和 `pending_merged`（需要近似的合并会话数）。

### 2.4 备份 + 磁盘余量

```bash
# 备份（含 WAL/SHM），放在数据卷同级目录
cp -a ./data ./data.bak-$(date +%Y%m%d-%H%M)

# 确认剩余空间至少为库大小的 10%
df -h .
du -sh ./data/gateway.db
```

---

## 3. 正式执行

镜像里没有 `scripts/`，把脚本拷进容器再跑（一次性动作，容器重建后需重拷）。

### 3.1 拷入脚本

```bash
docker cp scripts/backfill_turn_count.py "$CONTAINER":/app/backfill_turn_count.py
```

### 3.2 只读预演（不写库）

```bash
docker exec -i "$CONTAINER" python /app/backfill_turn_count.py --dry-run
```

预期输出形如：

```
数据库：/data/gateway.db
总日志 21435 条；待回填 21435 条（其中续写合并行约 1830 条）
dry-run，未做任何写入。
```

### 3.3 小批试跑（测吞吐，约 1–2 分钟）

```bash
docker exec -i "$CONTAINER" python /app/backfill_turn_count.py \
  --batch-size 2000 --max-rows 20000
```

输出里会打印「累计 N，X 行/秒」。用 `pending ÷ 行/秒` 估算全量耗时。

### 3.4 全量回填（二选一，按窗口选）

**A. 在线低峰（不停服）**

```bash
docker exec -i "$CONTAINER" python /app/backfill_turn_count.py \
  --batch-size 5000 --sleep 0.1 --max-seconds 900
```

`--max-seconds` 到点自动停，可反复执行直到「剩余 0 条」。

**B. 维护窗口（停服更快）**

```bash
docker stop "$CONTAINER"          # 停服后容器仍在，可继续 exec
docker exec -i "$CONTAINER" python /app/backfill_turn_count.py --batch-size 50000 --sleep 0
docker start "$CONTAINER"
```

> 建议：`pending_merged` 超过 5 万行选 B；否则选 A。

### 3.5 进度判断

每批输出一行：`[n] id 起始-结束：本批 X 行（单轮 a / 合并 b），累计 N，M 行/秒`。
最后一行 `完成：处理 N 行（合并行 b），用时 Ts，剩余 0 条` 即结束。
中断了直接重跑同一条命令，只会处理 `turn_count IS NULL` 的行。

---

## 4. 验收（必须全部满足）

### 4.1 数据完整性

```bash
docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
con = sqlite3.connect("/data/gateway.db", timeout=30)
print("未回填:", con.execute("SELECT count(*) FROM request_logs WHERE turn_count IS NULL").fetchone()[0])
print("非法值:", con.execute("SELECT count(*) FROM request_logs WHERE turn_count IS NOT NULL AND turn_count < 1").fetchone()[0])
print("合并会话:", con.execute("SELECT count(*) FROM request_logs WHERE turn_count > 1").fetchone()[0])
print("最大调用数:", con.execute("SELECT max(turn_count) FROM request_logs").fetchone()[0])
PY
```

- 未回填 = `0`
- 非法值 = `0`
- 合并会话 > `0`（生产一定存在 agent 会话）
- 最大调用数 > `1`

### 4.2 10/22 样本（对照报告里的异常会话）

```bash
docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
con = sqlite3.connect("/data/gateway.db", timeout=30)
print(con.execute("""
  SELECT count(*) rows, sum(coalesce(turn_count,1)) calls,
         sum(CASE WHEN turn_count IS NULL THEN 1 ELSE 0 END) unbackfilled
  FROM request_logs WHERE date(created_at,'+8 hours')='2026-10-22'
""").fetchone())
PY
```

回填后 `calls` 应明显大于 `rows`（此前这条查询 `calls == rows`）。

### 4.3 概览页生效

概览读的是快照缓存，强制重算一次：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<ADMIN_PASSWORD>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8000/api/admin/dashboard/stats?days=7&refresh=1' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["summary"])'
```

预期：`requests` 等于调用数（大于日志行数）；`avg_latency_ms` / `p95_latency_ms` 是秒级
（不会再出现几十分钟）。前端「概览 → 每日调用次数」的「合计」同步变大。

---

## 5. 回滚与故障处理

| 情况 | 处理 |
|---|---|
| 想撤销回填 | `UPDATE request_logs SET turn_count=NULL, last_latency_ms=NULL;`（统计退回按 1 次计，数据无损） |
| 执行中断 | 直接重跑同一条命令，幂等续跑 |
| `database is locked` | 加大 `--sleep`、减小 `--batch-size`；脚本已设 60s `busy_timeout` |
| 磁盘不足 | 先 `PRAGMA wal_checkpoint(TRUNCATE);` 回收 WAL，再 continue |
| 容器重建后脚本丢失 | 重新 `docker cp` 一次即可 |

**不要做**：不要删除 `request_logs` / `request_log_messages`；不要重算或清零
`latency_ms`、`total_tokens`（它们是真实消费）；不要 `DROP COLUMN`（线上无收益，
只会让统计退回旧口径）。

---

## 6. 回填规则（供核对，不需要执行）

- 单轮行（`updated_at` 为空或 `<= created_at`）：`turn_count = 1`、`last_latency_ms = latency_ms`，**精确**。
- 合并行（`updated_at > created_at`）：`turn_count = 不同消息写入时间戳的个数`（同一次写入的消息共享
  `created_at`，故近似调用次数，下限 2）；`last_latency_ms` 留空（单轮耗时不可恢复，延迟统计自动排除）。
- 超过 1000 条消息的会话会被滚动窗口截断，因此合并行的调用数是**下限**，前端展示为「≈N 次调用」。
