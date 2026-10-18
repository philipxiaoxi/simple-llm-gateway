# 运维现场报告：API 间歇性无响应 / 数据库连接池耗尽

- 提交对象：后端开发
- 编写方：运维
- 日期：2026-10-18
- 环境：Docker 生产环境（容器 `llm-gateway`，镜像 `simple-llm-gateway-gateway`）
- 现象：一段时间内 API 无响应或极慢，重启容器后恢复
- 当前状态：容器已于 2026-10-17 10:29 重建并运行，当前表现正常；**根因尚未修复，仍可能复发**
- 文档性质：运维提供的现场证据、已采集材料、可继续补充的材料，以及给开发者的排查/修复建议
- 说明：开发者没有服务器权限，本报告中的服务器命令均由运维执行；开发者只需阅读结论和证据，并按第 8 节编号向运维提出需要补充的材料
- 使用方式：本报告可直接转发给后端开发，无需补充上下文

**阅读指引：**

- 想快速了解故障经过：看第 1、3 节
- 想定位代码问题：看第 4、5 节
- 想安排修复：看第 5 节 Phase 1～5
- 需要更多现场材料：看第 8 节，把编号发给运维
- 想复核运维采集过程：看第 9 节

---

## 1. 结论摘要

本次故障不是模型上游慢，也不是容器 CPU/内存打满。

直接原因：

1. SQLAlchemy 默认连接池只有 **5 + 10 = 15 个连接**；
2. 故障窗口内这 15 个连接全部被占用，新请求在 `pool.connect()` 等待 **30 秒**后失败；
3. 日志中大量出现：

   ```text
   sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
   connection timed out, timeout 30.00
   ```

4. 重启容器会清空进程内连接池和事务状态，因此“重启后正常”，但这只是掩盖问题，不是修复。

需要特别说明：**从日志看，用户最近一次对话调用不是本次故障的直接原因。** 该请求在故障窗口开始前约 13 分钟已经正常返回 200。真正进入连接池耗尽的时间点是之后的管理端/后台负载时段。

---

## 2. 影响与当前状态

- 故障窗口（日志时间，UTC）：约 **2026-10-18 09:04:58 – 09:09:34**
- 影响：
  - 代理 API 和后台任务无法获取数据库连接；
  - 后台 info / generation / knowledge / offline 循环成批报错；
  - 管理端图片/资源接口出现 30 秒超时后 500；
  - 用户感知为“API 没反应 / 很慢”。
- 当前：
  - 重启后服务正常；
  - 数据库文件仍约 **9GB**；
  - 连接池仍是默认 15 个，长 Session 的代码路径仍存在；
  - 同类问题具备再次发生条件。

---

## 3. 现场证据

### 3.1 日志时间线

使用 `docker logs -t llm-gateway --since 24h` 采集，以下为关键节点（UTC）：

```text
08:50:30  LiteLLM completion 开始（用户对话请求）
08:51:41  POST /v1/chat/completions 200 OK        ← 该请求约 71 秒后正常完成
08:51:38  ClientDisconnect                         ← 客户端断开/重试
08:54:34  ClientDisconnect                         ← 客户端再次断开/重试
08:55–09:04  管理端 dashboard / stats 等接口持续 200
09:04:57  1.5 秒内管理端发起 17 个请求，其中 12 个为媒体资源请求
09:04:58  后台 worker 开始无法获取连接
09:05:28  第一条 QueuePool timeout（正好等待 30 秒）
09:05–09:08  offline / knowledge / generation / info 等循环成批超时
09:08:28  管理端 generation 资源请求开始等待连接
09:08:58  6 个 get_asset 请求 30 秒超时后 500
09:09:34  dashboard 请求恢复 200
```

关键日志原文：

```text
starlette.requests.ClientDisconnect
  File "/app/app/routers/proxy.py", line 115, in chat_completions
    body = await request.json()
```

说明：这两次 `ClientDisconnect` 发生在读取请求体阶段，尚未进入数据库鉴权，属于客户端主动断开/重试，不是数据库连接池异常。

连接池超时原文：

```text
[offline-jobs] worker error: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00

[info] AI 判定循环异常: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00

sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00
  File "/app/app/routers/admin_generation.py", line 137, in get_asset
    asset = db.get(GenerationAsset, asset_id)
```

### 3.2 当前数据库与数据量（现场快照）

采集时间：**2026-10-18 09:49 UTC**（容器时间与宿主机一致）。以下均为只读查询，未修改数据。

#### 3.2.1 文件与磁盘占用

| 项目 | 当前值 |
|---|---|
| `data/gateway.db` | 9,011,806,208 字节（8.4 GiB / 约 9.0 GB） |
| `data/gateway.db-wal` | 8,746,792 字节（8.3 MiB） |
| `data/gateway.db-shm` | 32,768 字节（32 KiB） |
| `data/` 总体积 | 约 17 GB |
| `PRAGMA page_count` | 2,200,150 |
| `PRAGMA page_size` | 4096 |
| `PRAGMA freelist_count` | 0 |
| `PRAGMA journal_mode` | wal |
| `PRAGMA auto_vacuum` | 0（关闭） |
| `PRAGMA wal_autocheckpoint` | 1000 |
| 应用连接 PRAGMA（`backend/app/db.py`） | `busy_timeout=10000`、`synchronous=NORMAL`、`cache_size=-65536`、`mmap_size=268435456`、`foreign_keys=ON` |

`data/` 子目录占用：

| 目录/文件 | 体积 |
|---|---:|
| `gateway.db` | 8.4 G |
| `tools` | 3.9 G |
| `info` | 2.5 G |
| `offline` | 904 M |
| `douyin` | 359 M |
| `generation` | 179 M |
| `chroma` | 170 M |
| `sites` | 51 M |
| `models_dev_cache.json` | 5.6 M |
| 其它（skills/knowledge_jobs 等） | 约 5 M 以内 |

说明：`tools`、`info`、`offline` 主要是磁盘文件，不占 SQLite 体积；真正放大数据库的是 `request_log_messages`。

#### 3.2.2 主要表行数

| 表 | 行数 |
|---|---:|
| `request_log_messages` | **1,820,064** |
| `request_logs` | **9,133** |
| `content_audit_findings` | 16,489 |
| `content_audit_scans` | 5,814 |
| `info_items` | 2,566 |
| `info_media` | 3,292 |
| `desktop_tool_run_logs` | 1,670 |
| `mcp_call_logs` | 462 |
| `benchmark_results` | 449 |
| `generation_assets` | 134 |
| `generation_tasks` | 118 |
| `voice_segments` / `voice_events` / `voice_sessions` | 0 / 0 / 0 |
| `knowledge_chunks` | 172 |
| `site_versions` | 36 |

#### 3.2.3 `request_log_messages` 正文体积

| 项目 | 当前值 |
|---|---|
| `content_json` 总体积 | **7,349.2 MiB（约 7.2 GiB）** |
| 单条最大值 | **4,950,700 字节（约 4.95 MiB）** |
| 单条平均值 | 4,234 字节 |
| 行数 | 1,820,064 |

体积分布：

| 单条大小 | 行数 | 合计体积 |
|---|---:|---:|
| `< 1 KB` | 1,232,436 | 388.3 MiB |
| `1–16 KB` | 534,728 | 1,774.6 MiB |
| `16–256 KB` | 52,188 | **4,867.8 MiB** |
| `256 KB–1 MB` | 680 | 241.6 MiB |
| `>= 1 MB` | 32 | 76.9 MiB |

结论：**约 4.9 GiB 集中在 5.2 万条 16–256KB 的消息里**，不是少量超大消息造成的；这是长期会话/工具调用完整落库的结果。

单会话体积最大的日志（Top 8）：

| `log_id` | 消息数 | 正文体积 | 单条最大 |
|---:|---:|---:|---:|
| 4200 | 55,641 | 154.9 MiB | 309,162 B |
| 768 | 85,340 | 75.7 MiB | 146,294 B |
| 8627 | 1,000 | 66.3 MiB | 2,989,262 B |
| 4197 | 20,095 | 38.6 MiB | 84,760 B |
| 4209 | 10,573 | 24.6 MiB | 84,760 B |
| 787 | 10,764 | 23.8 MiB | 162,214 B |
| 1588 | 5,860 | 18.3 MiB | 33,622 B |
| 8988 | 962 | 12.1 MiB | 1,066,469 B |

补充：`log_id=4200` 和 `768` 是 2026-08-19～08-30 的历史会话，消息数分别 5.5 万 / 8.5 万，明显超过当前 `MAX_LOG_MESSAGES=1000` 的滚动窗口，属于旧版本遗留数据。2026-10 之后的多数长会话稳定在 1000 条、约 10.8 MiB。

#### 3.2.4 `request_logs` 概况

| 项目 | 当前值 |
|---|---|
| 时间范围 | 2026-08-15 13:55 – 2026-10-18 08:51（UTC） |
| 总数 | 9,133 |
| 成功 / 错误 | 9,018 / 115 |
| 流式 / 非流式 | 8,926 / 207 |
| `session_key` 有值 | 247 |
| `session_key` 为空 | **8,886（97.3%）** |
| `reasoning_json` 总体积 | 296.9 MiB |
| `reasoning_json` 单条最大 | 2,167,648 字节（约 2.07 MiB） |
| `request_body` / `response_body` 非空 | 0 / 0（已迁移到消息表） |

耗时特征：

| 类型 | 平均 `latency_ms` | 最大 `latency_ms` |
|---|---:|---:|
| 流式 | 45,395 ms（约 45 秒） | **16,971,735 ms（约 4 小时 42 分）** |
| 非流式 | 9,799 ms（约 10 秒） | 805,623 ms（约 13.4 分钟） |

说明：这是代理请求的端到端耗时，包含客户端流式消费时间，不等于 SQL 耗时；但说明存在极长连接和长会话。

请求量较高的日期（按 `request_logs` 创建日统计）：

| 日期 | 请求数 | Token（千） |
|---|---:|---:|
| 2026-10-09 | 777 | 264,023.6 |
| 2026-10-10 | 700 | 349,207.7 |
| 2026-08-24 | 568 | 48,260.5 |
| 2026-08-20 | 547 | 46,478.1 |
| 2026-09-05 | 521 | 60,335.0 |
| 2026-10-04 | 501 | 112,003.7 |
| 2026-08-25 | 470 | 30,892.2 |
| 2026-08-31 | 434 | 73,321.2 |
| 2026-08-19 | 419 | 40,366.1 |
| 2026-08-16 | 407 | 24,510.7 |

最大值出现在 2026-10-05（单日 421,042.2 千 Token，14 条请求），说明存在少量超大上下文请求。

#### 3.2.5 只读扫描耗时（本次复测）

| 查询 | 耗时 |
|---|---:|
| `SELECT count(*) FROM request_log_messages` | 26.8 秒 |
| `count + sum(length(content_json))` | 78.3 秒 |
| 按大小分桶统计 | 105.5 秒 |
| 按 `log_id` 聚合 Top 20 | 77.0 秒 |

结论：数据库已经大到任何全表扫描、错误查询或长时间写事务都会明显放大故障持续时间；同时 `content_json` 的写入和更新会持续推高 WAL/写锁占用。

### 3.3 容器资源

当前（2026-10-18 09:49 UTC）采样：

```text
llm-gateway  CPU 0.53%  MEM 775.9MiB / 13.14GiB  MEM 5.77%  BLOCK I/O 4.25GB / 807MB
```

故障时较早一次采样：

```text
llm-gateway  CPU 0.33%  MEM 753.1MiB / 13.14GiB  BLOCK I/O 3.44GB / 807MB
```

- CPU 没有打满；
- 内存没有打满；
- 因此不是资源瓶颈，而是连接池/数据库锁/长事务问题。

### 3.4 已排除项

- 用户最近一次对话在 08:51:41 已返回 200，不在 09:04:58 开始的故障窗口内；
- 两次 `ClientDisconnect` 是客户端断开，不是连接池报错；
- 内容审计最近没有运行（`content_audit_scans` 最近记录为 2026-09-06）；
- 资讯采集最近一轮在 04:16 左右完成；
- 站点部署最近一次在 08:27 左右完成；
- 容器 CPU/内存正常。

因此，故障触发点落在 **09:04:58 前后的管理端请求/后台循环**，但仅凭现有日志无法确认具体是哪一个 Session 占住了 15 个连接。

### 3.5 现有日志盲区：没有数据库慢查询/慢请求日志

当前项目**没有**数据库慢查询日志，也**没有**请求耗时日志。现有日志只能看到“谁在等连接”，看不到“谁占着连接”。

| 能力 | 现状 | 说明 |
|---|---|---|
| uvicorn access log | 有 | 只记录方法、路径、状态码，**不记录耗时** |
| SQLAlchemy `echo` | 未开启 | 不打印 SQL 与执行时间 |
| 慢 SQL 阈值日志 | 无 | 没有 `>N ms` 的 SQL 日志 |
| 连接 checkout/checkin 持有时间 | 无 | 无法知道单个连接被持有了多久 |
| 连接池状态日志 | 无 | 无法看到 checkedout/overflow |
| 慢请求耗时日志 | 无 | 只有 uvicorn access log，无 duration |
| 未处理异常堆栈 | 有 | QueuePool timeout、ClientDisconnect 等会打印堆栈 |
| `request_logs.latency_ms` | 有 | 代理请求端到端耗时（含上游等待），**不是 SQL 耗时** |
| 启动预热耗时 | 有 | 仅 `>50ms` 时打印一次 `[startup]` |

这导致本次故障只能看到等待者：

```text
QueuePool limit of size 5 overflow 10 reached
```

但看不到是哪一个 Session、哪一条 SQL、哪一个接口把连接占住了 15 个。也无法区分是：

- 慢 SQL / 全表扫描；
- SQLite 写锁等待；
- 长事务/长 Session；
- 线程池排队导致 `close()` 延迟；
- 真正的连接泄漏。

因此本次只能给出“可疑代码路径”，无法给出确定的占用点。**Phase 1 的可观测性改造是后续所有修复的前提。**

建议补充（不要在生产长期开 `echo=True`，日志量和参数泄露风险都不可控）：

1. SQLAlchemy `checkout/checkin` 事件：持有超过阈值时打印堆栈；
2. 慢请求中间件：打印 `[slow] 耗时 方法 路径 状态码`；
3. 可选：SQL 执行耗时事件，只记录超过阈值的语句摘要（不记录参数值）；
4. 连接池状态：`pool.status()` 定期日志或管理员诊断接口。

---

## 4. 根因机制

### 4.1 SQLAlchemy Session 与连接的关系

SQLAlchemy 的 Session 第一次执行 SQL 时会从连接池取一个连接；连接会跟随 Session 的事务，直到：

- `session.commit()`
- `session.rollback()`
- `session.close()`

才会归还连接池。

项目中的请求级 Session：

```python
# backend/app/db.py:925
def get_db():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

也就是说：**连接不是在每条 SELECT 后释放，而是在整个请求结束时才释放。**

### 4.2 默认连接池过小

`backend/app/db.py` 创建 Engine 时没有显式配置连接池，使用 SQLAlchemy 默认值：

```text
pool_size = 5
max_overflow = 10
pool_timeout = 30s
=> 最大 15 个连接
```

当 15 个连接全部被占用，新请求会在 `pool.connect()` 阻塞 30 秒，然后抛 `QueuePool limit`。

### 4.3 长 Session 会放大问题

当前代码中存在多个“Session 跨越网络请求或长任务”的路径，任何一个在高峰期都可能长时间占用连接：

| 位置 | 问题 |
|---|---|
| `backend/app/services/proxy.py` `handle_chat` | 非流式请求 `await call_chat(...)` 等待上游时，请求级 Session 仍开着，最长受 `REQUEST_TIMEOUT_SECONDS=120` 限制 |
| `backend/app/mcp_server.py:79` | 一个 Session 跨越整个 `invoke_tool`，可能包含图片/视频生成、文档解析、知识库向量化、抖音下载等长任务 |
| `backend/app/services/voice_session.py:372` | 语音纠错拿着 Session 等待 LLM 返回 |
| `backend/app/services/content_audit.py:814` | 一个 Session 贯穿整轮内容审计扫描，只按批 commit |
| `backend/app/routers/local_agent.py:54` | Session 跨越 `await list_account_models(account)` 网络请求 |
| 其他 `get_session_factory()` 调用点 | 需要逐个确认 `close()` 前是否包含 `await`、子进程、解压、大循环或 `sleep` |

### 4.4 为什么整站像“死了一样”

`backend/app/routers/proxy.py` 的鉴权在 `async def` 中直接执行同步 SQLAlchemy 查询：

```python
# backend/app/routers/proxy.py:111
resolved, error = await _authenticate(request, db, protocol, authorization, x_api_key)

# 内部：
# api_key = resolve_api_key(db, raw)   # 同步数据库查询
```

连接池满时，这个同步查询会阻塞事件循环最多 30 秒。单进程 uvicorn 的事件循环被阻塞后，健康检查、静态资源和所有 API 都无法处理，表现为整站无响应。

### 4.5 9GB 数据库是放大器

- 任何慢查询、全表扫描、统计聚合都会占用连接更久；
- 任何写事务都会让其他写 Session 等待 `busy_timeout=10000`；
- 大量等待写锁的 Session 又会继续占用连接池；
- 最终形成“写锁等待 → 连接不归还 → 新请求拿不到连接 → 30 秒超时”的连锁反应。

---

## 5. 请开发者确认/实现的事项

### Phase 1：可观测性（优先，低风险）

目标：下次故障能直接定位到“哪个接口、哪个线程、哪一行代码持有了连接多久”。

1. `backend/app/db.py`：给 Engine 注册 `checkout/checkin` 事件
   - `checkout` 时记录 `time.monotonic()` 和 `traceback.format_stack()`；
   - `checkin` 时如果持有时间超过阈值，打印 `[db-hold] 持续时间 + 堆栈`；
   - 建议新增配置 `DB_LONG_HOLD_SECONDS=5`。

2. `backend/app/main.py`：增加慢请求中间件
   - 记录 `[slow] 耗时 方法 路径 状态码`；
   - 建议阈值 `SLOW_REQUEST_SECONDS=2`；
   - 流式请求按整个响应时长统计。

3. 增加连接池状态输出（日志或仅管理员可见的诊断接口）
   - `pool.status()`：`size`、`checkedin`、`checkedout`、`overflow`。

验收：

- 人为制造一个 10 秒慢请求，日志中必须出现该请求的 `[db-hold]` 堆栈和 `[slow]` 记录；
- 再次出现连接池耗尽时，日志能指出占用者，而不是只有等待者。

### Phase 2：消除长 Session（核心修复）

原则：**网络请求、模型调用、文件解压、子进程、长时间循环期间不得持有数据库连接。**

1. 代理请求
   - `backend/app/routers/proxy.py`、`backend/app/services/proxy.py`
   - 鉴权、模型解析、凭据准备、读取 reasoning 使用短 Session；
   - 关闭 Session 后再 `await` 上游；
   - 请求日志继续使用独立 Session 写入；
   - 注意 `prepare_credential()` 可能刷新 OAuth token，需要在关闭前正确提交，不能直接 rollback 丢掉刷新结果。

2. MCP 工具
   - `backend/app/mcp_server.py:79`
   - 短会话读取 `McpKey` 和能力配置；
   - 关闭后执行长任务；
   - 结果落库使用新会话。

3. 语音纠错
   - `backend/app/services/voice_session.py:372`
   - 短会话读取 account/model/credential；
   - 关闭后调用 LLM；
   - 结果用新会话写回。

4. 内容审计
   - `backend/app/services/content_audit.py:814`
   - 改为每批创建 Session，处理完 commit 后立刻 close；
   - 不要用一个 Session 贯穿整轮扫描。

5. 本地 Agent 模型刷新
   - `backend/app/routers/local_agent.py:54`
   - 关闭 Session 后再 `await list_account_models(...)`；
   - 保存结果时重新开短会话。

6. 全项目排查
   - 搜索所有 `get_session_factory()()`；
   - 确认从创建到 `close()` 之间不存在 `await`、`subprocess`、`zipfile/tarfile`、大循环、`sleep`。

验收：

- 并发 20 个非流式慢请求 + 管理端首页刷新，不再出现 `QueuePool limit`；
- `[db-hold]` 日志中不再出现请求级连接长期超过 5 秒。

### Phase 3：连接池与 SQLite 策略

方案 A（推荐，针对当前 SQLite）：

```python
from sqlalchemy.pool import NullPool

create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
```

效果：

- 不再有固定 15 个连接的上限；
- 单个慢请求不会占死其他请求的连接槽；
- SQLite 写锁等待仍由 `busy_timeout` 控制。

注意：

- 必须保留 Phase 1 的长持有日志，防止真正的 Session 泄漏变成文件描述符泄漏；
- 确认容器 `ulimit -n` 足够；
- 每个新连接会重新执行 PRAGMA，会有少量额外开销。

方案 B（暂时保留 QueuePool）：

```python
create_engine(
    ...,
    pool_size=20,
    max_overflow=20,
    pool_timeout=5,
    pool_recycle=1800,
    pool_pre_ping=True,
)
```

同时把 `pool_timeout` 从 30 秒降到 5 秒，避免单个请求阻塞事件循环 30 秒。

注意：方案 B 只是缓解，长 Session 仍可能耗尽更大的连接池。

### Phase 4：数据库瘦身与保留期

当前 `request_log_messages` 约 182 万行、正文约 7.3GB，且没有整体请求日志保留期。

建议：

1. 新增保留期配置，例如：

   ```text
   REQUEST_LOG_RETENTION_DAYS=30
   REQUEST_LOG_BATCH_SIZE=500
   REQUEST_MESSAGE_MAX_BYTES=262144
   ```

2. 每天低峰期分批删除超期 `RequestLog`，依靠外键级联删除消息；
   - 每批 500～1000 条；
   - 每批提交后 sleep 0.2～0.5 秒，避免长时间占写锁。

3. 入库前截断超大 `content_json`；
   - 避免单条 4.95MB 的记录；
   - 超大正文建议落文件，DB 只存路径和摘要。

4. 历史 9GB 空间不会因为 DELETE 自动缩小：
   - 停服、备份后执行 `VACUUM`；
   - 或使用 `VACUUM INTO` 生成新库后替换；
   - 注意需要额外磁盘空间和停机窗口。

5. 建议把 `./data` 从 HDD bind mount 移到 SSD；
   - 当前 Docker 挂载：`./data:/data`；
   - 9GB SQLite 的随机读写和 checkpoint 在 HDD 上尾延迟明显。

### Phase 5：长期结构

如果用户量或后台任务继续增长：

- 把在线 API 与后台 job 拆分为独立进程/容器；
- 或迁移 PostgreSQL；
- 不建议给 SQLite 增加多个 uvicorn worker，多进程会加剧写锁竞争。

---

## 6. 运维侧说明与可配合事项（供开发者了解）

以下内容用于让开发者了解运维已经做了什么、还能提供什么，不需要开发者执行服务器命令。

1. **本次未修改代码**，现场保持原样，等待开发按 Phase 1～5 实施修复。
2. **本次故障已重启恢复**，但重启前的线程栈、连接池 checked-out 状态没有来得及抓取，这是当前证据链的主要缺口。
3. **复发时，运维会先采集再重启**：
   - `docker logs -t` 完整日志；
   - `docker stats` 和主机 IO；
   - `pool.status()`；
   - `/proc/1/fd` 中 `gateway.db*` 句柄数；
   - `py-spy dump --pid 1` 连续 3 次。
   采集后会按第 8 节整理给开发。
4. **运维可提供的环境信息**：镜像 digest、容器启动时间、挂载方式、CPU/内存限制、日志驱动、反向代理耗时日志、宿主机磁盘类型和 IO 指标、同宿主机其他高 IO 容器情况。
5. **监控侧可由运维补充**：
   - `QueuePool limit` 关键字告警；
   - 5xx 比例告警；
   - `/health` 响应时间告警；
   - 磁盘 IO await、SQLite WAL 大小、磁盘剩余空间告警。
6. **数据库瘦身需要开发先定策略**：保留期、单条消息大小上限、是否允许删除历史正文。策略确定后，运维负责备份、分批清理、VACUUM 和停机窗口安排。
7. **不要把“重启恢复”当作问题已解决**：数据库规模和长 Session 代码路径仍然存在，修复应以开发侧代码改造为主。

---

## 7. 总体验收标准

满足以下条件才认为风险已解除：

1. 故障复发时，日志能打印出占用连接超过 5 秒的完整堆栈和请求路径；
2. 并发 20 个慢代理请求 + 管理端刷新，不再出现 `QueuePool limit`；
3. 非流式代理等待上游期间不持有数据库连接；
4. MCP、语音、内容审计、本地 Agent 的长任务不再跨网络/长循环持有 Session；
5. 数据库大小回到可控范围（建议先降到 1GB 以内），且请求日志有保留期；
6. 生产库迁移到 SSD，或完成 PostgreSQL/进程隔离方案评估。

---

## 8. 运维可提供的现场材料（开发者无需服务器权限）

开发者没有服务器现场，仅靠“现象描述”很难定位。以下材料由运维采集并提供，开发者不需要自己执行命令；如需补充，请把下面的编号（如 `8.4`）发给运维。

本次已经提供：

| 编号 | 材料 | 对应内容 |
|---|---|---|
| 3.1 | 带时间戳的关键日志时间线 | 故障开始/恢复、QueuePool、ClientDisconnect |
| 3.2 | 数据库规模统计 | 9GB、182 万消息、7.3GB 正文、扫描耗时 |
| 3.3 | 容器资源采样 | CPU 0.33%、内存 753MB，排除资源打满 |
| 3.4 | 已排除项 | 对话请求、内容审计、采集、站点部署等时间点 |

可按需补充：

| 编号 | 材料 | 能回答的问题 |
|---|---|---|
| 8.1 | 完整应用日志 + 反向代理耗时日志 | 请求到底卡在哪一段、持续多久 |
| 8.2 | 反向代理 `$request_time` / `$upstream_response_time` | 耗时是在网关、排队还是应用内部 |
| 8.3 | 容器/镜像/挂载/限制信息 | 环境是否与开发预期一致 |
| 8.4 | py-spy 线程栈、DB fd 数、pool.status() | 故障时到底哪一行占着连接 |
| 8.5 | SQLite 文件、schema、索引、查询计划 | 是否是慢 SQL、全表扫描、索引缺失 |
| 8.6 | 故障窗口 request_logs 摘要 | 是否有异常流量、重试、长请求 |
| 8.7 | 后台任务状态 | 是否有采集/审计/生成任务同时运行 |
| 8.8 | 主机 IO、磁盘、内核日志 | 是否是 HDD IO 饱和或文件系统错误 |
| 8.9 | 脱敏配置和版本信息 | 超时、连接池、日志保留等配置 |
| 8.10 | 运维时间线 | 重启、发布、备份、其他容器操作 |
| 8.11 | 脱敏数据库副本/最小子集 | 开发本地复现查询和索引问题 |

材料目录建议：

```text
incident-20261018/
  00-manifest.txt          采集时间、时区、镜像 digest、git commit、采集人
  01-timeline.md           故障时间线、重启/发布/备份/其他容器操作
  02-docker/               docker inspect、events、stats、日志配置
  03-logs/                 应用日志、反向代理日志、系统日志
  04-db/                   文件大小、schema、索引、统计信息、查询计划
  05-requests/             故障窗口 request_logs 摘要（脱敏）
  06-host/                 CPU/内存/磁盘 IO/文件系统/内核日志
  07-config/               脱敏后的配置、compose、环境变量清单
  08-stacks/               py-spy / gdb / 线程栈（故障时抓取）
```

> 以下小节中的命令是运维的采集方式，供开发者复核材料和提出补充需求；开发者不需要在服务器上执行。

### 8.1 应用日志（最高优先级，必须带时间戳）

```bash
date -u
date
timedatectl
docker logs -t llm-gateway --since 6h > incident-20261018/03-logs/app-6h.log
docker logs -t llm-gateway --since 24h > incident-20261018/03-logs/app-24h.log
```

需要保留：

- 故障前至少 30 分钟、故障全过程、恢复后至少 30 分钟；
- 完整异常堆栈，不要只截最后一行；
- 后台任务日志：`[offline-jobs]`、`[knowledge-jobs]`、`[generation]`、`[info]`、`[voice]`、`[content-audit]`；
- 所有 `QueuePool limit`、`ClientDisconnect`、`TimeoutError`、`database is locked`、`disk I/O error`。

同时确认 Docker 日志有没有被轮转截断：

```bash
docker inspect llm-gateway --format '{{json .HostConfig.LogConfig}}'
docker inspect llm-gateway --format '{{.LogPath}}'
```

### 8.2 反向代理日志（当前最缺的耗时信息）

应用自身的 uvicorn access log **没有耗时**。如果前面有 Nginx / Traefik / Caddy，运维提供故障窗口的访问日志时应至少包含：

- `$request_time`：客户端看到的完整耗时；
- `$upstream_response_time`：到容器的耗时；
- `$status`；
- `$request_method`、`$request_uri`；
- `$upstream_addr`；
- `$request_id`（如果配置了）。

这能直接区分：

- 请求卡在网关等待；
- 请求进入容器后卡在数据库；
- 请求进入了哪个接口。

建议 Nginx 临时/长期使用类似格式：

```nginx
log_format timed '$remote_addr - $remote_user [$time_local] '
                 '"$request" $status $body_bytes_sent '
                 '"$http_referer" "$http_user_agent" '
                 'rt=$request_time urt=$upstream_response_time '
                 'uaddr=$upstream_addr rid=$request_id';
```

### 8.3 容器与镜像信息

```bash
docker ps -a --no-trunc --format '{{json .}}' > incident-20261018/02-docker/ps.json
docker inspect llm-gateway > incident-20261018/02-docker/inspect.json
docker image inspect simple-llm-gateway-gateway > incident-20261018/02-docker/image.json
docker version > incident-20261018/02-docker/docker-version.txt
docker info > incident-20261018/02-docker/docker-info.txt
docker stats --no-stream > incident-20261018/02-docker/stats.txt
docker events --since '2026-10-18T08:30:00' --until '2026-10-18T09:30:00' \
  > incident-20261018/02-docker/events.txt
```

重点信息：

- 容器启动时间、重启次数、退出码；
- 镜像 digest、构建时间；
- `HostConfig`：CPU/内存限制、ulimit、日志驱动、挂载；
- `Mounts`：`data` 是否仍是 HDD bind mount；
- 环境变量（**必须脱敏**）；
- 健康检查配置。

### 8.4 进程与线程现场（故障时抓，重启后就没了）

如果还能进入故障现场，优先抓：

```bash
# Python 线程栈（推荐提前在镜像中安装 py-spy）
docker exec llm-gateway py-spy dump --pid 1 --locals \
  > incident-20261018/08-stacks/pyspy-$(date +%s).txt

# 连续抓 3 次，间隔 5 秒，便于判断是卡住还是在推进
for i in 1 2 3; do
  docker exec llm-gateway py-spy dump --pid 1 \
    > incident-20261018/08-stacks/pyspy-$i.txt
  sleep 5
done
```

如果镜像里没有 py-spy，可以提前准备一个带 `SYS_PTRACE` 的调试容器：

```bash
docker run --rm --pid=container:llm-gateway --cap-add SYS_PTRACE \
  -v "$PWD/incident-20261018/08-stacks:/out" python:3.11-slim \
  sh -c 'pip install -q py-spy && py-spy dump --pid 1 > /out/pyspy.txt'
```

其他进程信息：

```bash
docker exec llm-gateway sh -c 'cat /proc/1/status' > incident-20261018/08-stacks/proc-status.txt
docker exec llm-gateway sh -c 'ls /proc/1/task | wc -l' > incident-20261018/08-stacks/thread-count.txt
docker exec llm-gateway sh -c 'ls -l /proc/1/fd | wc -l' > incident-20261018/08-stacks/fd-count.txt
docker exec llm-gateway sh -c \
  'ls -l /proc/1/fd | grep -E "gateway.db|gateway.db-wal|gateway.db-shm"' \
  > incident-20261018/08-stacks/db-fds.txt
docker exec llm-gateway sh -c 'ss -s' > incident-20261018/08-stacks/sockets.txt
docker exec llm-gateway sh -c 'ss -tnp state established' > incident-20261018/08-stacks/established.txt
```

`db-fds.txt` 很有价值：可以直接看出进程里同时打开了多少个 `gateway.db` 连接。

连接池状态：

```bash
docker exec llm-gateway python -c \
  'from app.db import get_engine; print(get_engine().pool.status())' \
  > incident-20261018/08-stacks/pool-status.txt
```

### 8.5 SQLite 现场

文件与 WAL：

```bash
ls -lh data/gateway.db data/gateway.db-wal data/gateway.db-shm \
  > incident-20261018/04-db/files.txt
stat data/gateway.db data/gateway.db-wal data/gateway.db-shm \
  > incident-20261018/04-db/stat.txt
```

只读 PRAGMA：

```bash
python3 - <<'EOF' > incident-20261018/04-db/pragmas.txt
import sqlite3
c = sqlite3.connect('file:data/gateway.db?mode=ro', timeout=10)
for sql in [
    'PRAGMA journal_mode',
    'PRAGMA page_count',
    'PRAGMA page_size',
    'PRAGMA freelist_count',
    'PRAGMA cache_size',
    'PRAGMA busy_timeout',
    'PRAGMA wal_autocheckpoint',
    'PRAGMA synchronous',
]:
    print(sql, c.execute(sql).fetchall())
EOF
```

表结构与索引（该材料不涉及用户数据，可直接提供给开发者）：

```bash
python3 - <<'EOF' > incident-20261018/04-db/schema.sql
import sqlite3
c = sqlite3.connect('file:data/gateway.db?mode=ro', timeout=10)
for name, sql in c.execute(
    "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY type, name"
):
    print(f'-- {name}')
    print(sql or '')
    print()
EOF
```

常用查询计划（在**副本库**上跑，避免生产高峰）：

```bash
cp data/gateway.db /tmp/gateway-copy.db
python3 - <<'EOF' > incident-20261018/04-db/explain-plans.txt
import sqlite3
c = sqlite3.connect('/tmp/gateway-copy.db')
queries = [
    "SELECT * FROM request_logs ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 20",
    "SELECT api_key_id, SUM(total_tokens) FROM request_logs GROUP BY api_key_id",
    "SELECT log_id, MAX(seq) FROM request_log_messages GROUP BY log_id",
    "SELECT COUNT(*) FROM request_log_messages",
    "SELECT * FROM request_log_messages WHERE log_id = ? ORDER BY seq",
]
for q in queries:
    print('--', q)
    try:
        print(c.execute('EXPLAIN QUERY PLAN ' + q).fetchall())
    except Exception as e:
        print('ERROR', e)
EOF
```

如果条件允许，给开发者一份**脱敏后的数据库副本**或最小子集：

- `request_logs` 保留元数据（时间、状态、模型、账号、耗时、token），删除/截断用户正文；
- `request_log_messages` 只保留行数、大小分布、少量脱敏样本；
- 不要直接发送含用户 prompt、图片 Base64、密钥、访问令牌的完整库。

建议统计分布（在副本库上）：

```sql
-- 单条消息体积分布
SELECT
  CASE
    WHEN length(content_json) < 1024 THEN '<1KB'
    WHEN length(content_json) < 16384 THEN '1-16KB'
    WHEN length(content_json) < 262144 THEN '16-256KB'
    ELSE '>=256KB'
  END AS bucket,
  COUNT(*)
FROM request_log_messages
GROUP BY bucket;

-- 每个日志的消息数量分布
SELECT log_id, COUNT(*) AS n
FROM request_log_messages
GROUP BY log_id
ORDER BY n DESC
LIMIT 50;
```

### 8.6 故障窗口内的业务请求

运维可从 `request_logs` 导出故障前后 30 分钟的请求摘要（正文不导出）：

```sql
SELECT
  id, created_at, updated_at, protocol, model, stream, status,
  http_status, latency_ms, prompt_tokens, completion_tokens, total_tokens,
  account_name, api_key_name, session_key
FROM request_logs
WHERE created_at BETWEEN '2026-10-18 08:30:00' AND '2026-10-18 09:30:00'
ORDER BY created_at;
```

同时统计每分钟请求量：

```sql
SELECT substr(created_at, 1, 16) AS minute, COUNT(*), AVG(latency_ms), MAX(latency_ms)
FROM request_logs
WHERE created_at BETWEEN '2026-10-18 08:30:00' AND '2026-10-18 09:30:00'
GROUP BY minute
ORDER BY minute;
```

这能帮助判断故障时段是否有异常流量、重试风暴、长请求。

### 8.7 后台任务状态

提供故障前后各任务最近一次运行信息：

- `app_jobs` / `job_settings` 相关配置和运行时间；
- `content_audit_scans` 最近扫描时间；
- `info_sources.last_polled_at / last_success_at / last_error`；
- `generation_tasks`、`knowledge_ingest_jobs`、`offline_downloads` 在故障窗口内的状态变化；
- `voice_sessions/voice_segments` 故障窗口内的写入量；
- `mcp_call_logs` 故障窗口内的调用量/耗时。

如果这些信息只存在内存里（如 `JobRuntime`），重启后就丢了，需要故障时抓取管理端任务页或日志。

### 8.8 主机与磁盘 IO

SQLite 在 HDD 上对 IO 延迟非常敏感，需要主机侧数据：

```bash
iostat -x 1 5 > incident-20261018/06-host/iostat.txt
vmstat 1 5 > incident-20261018/06-host/vmstat.txt
free -h > incident-20261018/06-host/free.txt
df -h > incident-20261018/06-host/df.txt
df -i > incident-20261018/06-host/df-inode.txt
lsblk -o NAME,ROTA,MODEL,SCHED,SIZE,MOUNTPOINT \
  > incident-20261018/06-host/lsblk.txt
mount > incident-20261018/06-host/mount.txt
dmesg -T | tail -300 > incident-20261018/06-host/dmesg.txt
journalctl -k --since '2026-10-18 08:00' --until '2026-10-18 10:00' \
  > incident-20261018/06-host/kernel.txt
```

重点看：

- `%util`、`await`、`r_await`、`w_await` 是否有 IO 饱和；
- 是否发生 OOM、文件系统错误、磁盘错误；
- `data` 所在磁盘是 HDD 还是 SSD（`ROTA=1` 为 HDD）；
- 同一台机器上其他容器是否在大量读写（本次 `xyy-mysql`、`astrbot` 等容器也有较高 IO 累计）。

必要时补充：

```bash
iotop -o -b -n 3 > incident-20261018/06-host/iotop.txt
pidstat -d 1 5 > incident-20261018/06-host/pidstat.txt
smartctl -a /dev/sdX > incident-20261018/06-host/smart.txt
```

### 8.9 配置与版本信息（脱敏）

```bash
git rev-parse HEAD > incident-20261018/07-config/git-commit.txt
git status --short > incident-20261018/07-config/git-status.txt
docker inspect llm-gateway --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -E 's/(KEY|TOKEN|PASSWORD|SECRET)=.*/\1=***REDACTED***/I' \
  > incident-20261018/07-config/env-redacted.txt
cp docker-compose.yml incident-20261018/07-config/docker-compose.yml
cp Dockerfile incident-20261018/07-config/Dockerfile
```

需要明确提供给开发者的配置项：

- 镜像版本/commit；
- `DATABASE_PATH`、`FRONTEND_DIST`；
- `REQUEST_TIMEOUT_SECONDS`；
- 连接池相关配置（目前是默认值）；
- `busy_timeout`、`journal_mode`、`synchronous`；
- 日志保留期相关配置；
- 反向代理超时配置（`proxy_read_timeout` / `proxy_connect_timeout`）。

### 8.10 时间线与运维操作

本节由运维提供，用于解释“为什么恰好是 09:04:58 开始”。开发者可重点确认以下信息是否齐全：

- 故障开始/恢复的准确时间（UTC 和本地时间都要）；
- 是否重启过、谁重启的、重启原因；
- 故障前后是否发布/重建镜像；
- 是否执行过备份、VACUUM、批量导入、内容审计、站点部署；
- 是否调整过反向代理、防火墙、DNS；
- 同一台宿主机上是否有其他高 IO 容器/任务；
- 用户侧是否出现客户端重试、超时、切换网络；
- 管理端是否打开过大量图片/视频页面。

### 8.11 材料补充优先级

如果开发需要继续补充材料，可按这个顺序向运维提出（编号对应上面的小节）：

1. **8.1 / 8.2**：应用日志和反向代理耗时日志；
2. **8.4**：py-spy 线程栈 + `/proc/1/fd` 中 DB 连接数 + `pool.status()`；
3. **8.5**：SQLite 文件大小、WAL、schema/index、`EXPLAIN QUERY PLAN`；
4. **8.6**：故障窗口 `request_logs` 摘要；
5. **8.8 / 8.10**：主机 IO/内核日志、docker events、运维时间线；
6. **8.9**：脱敏配置和镜像 digest；
7. **8.5 中的脱敏副本**：脱敏数据库副本（最后考虑，注意隐私和体积）。

### 8.12 材料脱敏说明

运维提供材料时会执行以下脱敏，避免泄露生产密钥和用户数据：

- 不提供 `.env` 原文中的 `APP_SECRET_KEY`、上游 API Key、MCP Key、OAuth token、语音 token；
- 不提供带 `token=` 的媒体 URL、请求头 `Authorization`、用户 prompt/图片正文；
- 数据库副本会删除或截断 `request_log_messages.content_json`、`request_logs.response_body/reasoning_json`；
- IP、账号名、Key 名按需打码；
- 日志中的 `ClientDisconnect`、`QueuePool limit` 会保留，但不会贴出完整鉴权 Header。

---

## 9. 附录：运维执行过的只读采集命令（供开发者复核，不需要开发执行）

```bash
# 容器状态
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.BlockIO}}'

# 带时间戳日志
docker logs -t llm-gateway --since 24h > /tmp/llm-gateway.log

# 关键字
grep -nE "QueuePool limit|ClientDisconnect|TimeoutError|db-hold|slow" /tmp/llm-gateway.log

# SQLite 只读检查（避免在高峰期执行全表统计）
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('file:data/gateway.db?mode=ro', timeout=10)
for sql in [
    'PRAGMA page_count',
    'PRAGMA page_size',
    'PRAGMA freelist_count',
    'SELECT count(*) FROM request_logs',
    'SELECT count(*) FROM request_log_messages',
]:
    print(sql, c.execute(sql).fetchall())
EOF
```

注意：`count(*)`、`sum(length(...))` 在 7GB 级消息表上可能耗时数十秒，生产环境应放在低峰期或从备份库执行。
