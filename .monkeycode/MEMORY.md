# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[UI 必须同时适配 PC 与移动端]
- Date: 2026-08-29
- Context: 优化 /keys 页面顶栏时，用户指出只照顾移动端会破坏桌面布局
- Instructions:
  - 本项目同时支持移动端和 PC 端，改 UI 必须做响应式布局
  - 小屏用分层/网格避免换行挤压，大屏保持单行工具栏和桌面信息密度
  - 不要把移动端堆叠方案原样套到 PC

[移动端壳层与 iOS]
- Date: 2026-08-29
- Context: 要求站点按 iOS 体验适配，并同时保留 PC 布局
- Instructions:
  - 移动端顶栏固定，底部提供不超过 5 项的快捷 Tab
  - 在内容区中部向右滑动打开侧栏，向左滑动关闭；避开 iOS 左侧返回手势
  - PC 端继续使用左侧栏，不显示底部 Tab
  - iOS 进页必须保持深色底，避免先白后黑

[GitHub 推送凭据：本机直推，云端才走 socket]
- Date: 2026-09-01（2026-09-10 修正适用范围）
- Context: 推送并创建 PR 时的取凭据方式。用户明确：**本机开发环境直接推送即可，不需要 socket 流程**；只有 monkeycode 云端编程环境才必须走 unix socket 取凭据
- Category: Workflow & Collaboration
- Instructions:
  - **本机（开发者自己的 Mac）**：远端 `https://github.com/philipxiaoxi/simple-llm-gateway.git` 已配 `credential.helper=osxkeychain`，直接 `git push` 即可，不要绕 socket
  - **monkeycode 云端环境**：才需要下面这套 unix socket 取凭据 + x-access-token 推送的流程
  - 回复与日志中不得展示 token 明文，用 `sed -E 's/(basic )[A-Za-z0-9=]+/\1<redacted>/'` 之类方式打码
  - 云端取凭据：走 unix socket `/tmp/codingmatrix-git-credential.sock`，请求 `GET /git-credential?protocol=https&host=github.com`，推送时用户名固定为 `x-access-token`
  - 云端不要走 `gh auth git-credential` 或 helper 缓存，过期 token 会 401
  - 云端完整操作步骤（2026-09-02 实测验证）：
    1. 用 Python 的 `http.client.HTTPConnection` 子类连接 unix socket（重写 `connect()` 用 `socket.socket(AF_UNIX)`），请求 `GET /git-credential?protocol=https&host=github.com`
    2. 解析响应：优先按 JSON 取 `token`/`password`/`access_token`，失败则遍历行找 `password=`/`token=` 前缀
    3. 生成认证值：`base64("x-access-token:" + TOKEN)`，拼成 `basic <base64>`
    4. 若通过环境文件传递认证值，必须用 `shlex.quote()` 包裹，否则 `basic` 与 base64 之间的空格会被 shell 当成命令分隔符，导致 token 丢失
    5. 推送命令：`git -c credential.helper= -c "http.https://github.com/.extraheader=AUTHORIZATION: $AUTH_HEADER" push origin HEAD:<branch>`
    6. 完成后删除临时环境文件
  - 常见失败诊断：若提示 `could not read Username`，说明 extraheader 值没传进去（检查环境文件空格/引号问题）；若 401 说明 token 过期，重新从 socket 取

[本机创建 PR 走 GitHub API]
- Date: 2026-09-10
- Context: 分支推送后要开 PR，但本机没有安装 gh CLI
- Category: Workflow & Collaboration
- Instructions:
  - 本机没有 `gh`，建 PR 用 GitHub REST API：`POST https://api.github.com/repos/philipxiaoxi/simple-llm-gateway/pulls`
  - 凭据用 `git credential fill`（`protocol=https` + `host=github.com`）读取，全程不要打印 token 明文
  - 请求体：`{"title": ..., "head": "<分支>", "base": "main", "body": ...}`；创建后用 `GET /repos/{owner}/{repo}/pulls/{number}` 核对
  - 兜底：浏览器打开 `https://github.com/philipxiaoxi/simple-llm-gateway/pull/new/<branch>` 手动创建

[后端测试运行方式]
- Date: 2026-09-16
- Context: Agent 执行 MCP/知识库相关改动后跑回归测试
- Category: Testing Methods
- Instructions:
  - 必须在 `backend/` 目录下运行：`cd backend && PYTHONPATH=. python3 -m pytest ...`，否则 `app.*` 导入失败
  - 环境是全局 Python 3.11，没有可用 venv；装包用 `pip3 install --break-system-packages`
  - conftest 会注入 FakeEmbeddingClient 与临时 `MCP_CHROMA_PATH`，测试不依赖真实 embedding 上游
  - 前端类型检查：`cd frontend && npx tsc -b`（成功时无输出）

[知识库中文全文检索受 FTS5 分词限制]
- Date: 2026-09-16
- Context: 跨库全文检索测试用连续中文查询词匹配不到任何分块
- Category: Troubleshooting & Debugging
- Instructions:
  - `knowledge_chunks_fts` 用 SQLite FTS5 默认 unicode61 分词，连续中文串会被当成单个 token
  - 编写检索测试用例时，测试文本与查询词用空格分隔的 ASCII 单词，才能稳定命中全文检索
  - 中文场景的召回依赖向量检索；`mode=hybrid` 用 RRF 融合两路结果
  - embedding 配置变更后旧向量不可复用：若集合签名与新配置不一致，写入前会自动清空集合并按新签名重建

[AIHOT 模型榜改走 React Router .data 端点]
- Date: 2026-09-14（2026-09-29 更新）
- Context: 线上模型榜再次无数据并提示“结构可能已改版”，旧 RSC/HTML 选择器全部失配
- Category: Troubleshooting & Debugging
- Instructions:
  - 域名已从 aihot.virxact.com 301 到 aihot.news；2026-09 页面又从 Next.js RSC 迁到 React Router（Remix）SSR
  - 旧 class（lb-ranking-table/lb-name-cell/lb-rank-number/lb-score-cell/lb-price-cell）已全部消失，HTML/RSC 解析必然失败
  - 官方公开 API v1（https://aihot.news/openapi-v1.json，匿名只读）与 MCP（https://aihot.news/api/mcp）只覆盖资讯类（items/hot-topics/stories/dailies/selected/codex-resets），**没有模型榜端点**；/api/v1/leaderboard 返回 404
  - 模型榜数据源改为页面同源单次取数端点 `aihot_leaderboard_data_url`（默认 https://aihot.news/leaderboard.data；分类 /leaderboard/category/{coding|reasoning|knowledge|professional}.data），返回 React Router 序列化 JSON（扁平数组 + `_<下标>` 引用），resolve 后即 run/board/tabs/entries；`aihot_leaderboard_url` 仍作页面展示
  - entries 字段：rank、score、model{slug,name,provider,releasedAt}、sourceCount、coverage、confidence、stability{from,to}、price{input,output,cached,inputCny,outputCny,cachedCny,officialUrl}；上下文/输出上限仍由 models.dev 目录补
  - 真实抓取样例固定 `backend/tests/fixtures/aihot_leaderboard_rr.json`（30 条）；旧飞行载荷 `aihot_leaderboard_flight.rsc` 保留兼容回归；解析仍要求全行可解析，缺 slug/name/score 或行数对不上即整体报错、保留旧缓存
  - 手动排查：`curl -sS -H 'Accept: application/json, text/x-script, */*' https://aihot.news/leaderboard.data | python3 -m json.tool | head`
  - 站点条款：个人/公益/组织内部使用免费；对外商用、数据转售需书面授权

[站点部署 API：更新同一地址需传 slug]
- Date: 2026-09-27
- Context: 通过 REST 向站点部署服务上传 zip 并更新已有站点
- Category: Operations & Deployment
- Instructions:
  - Base URL：`https://xapi.xiaotao2333.top:344`，鉴权 `Authorization: Bearer <key>`，错误体为 `{"error":{"type","message"}}`
  - 部署：`POST /v1/sites`，`multipart/form-data`，文件字段名 `file`；返回 202 与 `slug`、`version_no`、`preview_url`
  - 异步：先 `unpacking`，轮询 `GET /v1/sites/{slug}/versions/{version_no}`；该接口直接返回版本对象（无 `version` 外层包装），看 `status` 到 `ready`/`duplicate`/`failed`
  - 关键：`POST` 默认会新建站点（slug 自动递增 site、site-2…）；要更新同一地址必须显式传表单字段 `slug=<已有slug>`，否则落到新站点
  - `name` 字段只作显示名，不控制 slug
  - 预览地址 `{origin}/sites/{slug}/`；`GET /v1/sites/{slug}/access` 查看访问模式与令牌，公开模式无 token
  - 删除站点 `DELETE /v1/sites/{slug}`（受 no-delete 规则约束，非必要不执行）

[站点部署只更新版本，不新建站点]
- Date: 2026-09-27
- Context: 用户发现多次上传生成了 site / site-2 / site-3 多个站点，要求以后统一更新
- Instructions:
  - 后续部署该站点一律使用原 slug：上传时表单带 `slug=site`，只产生新版本，不再新建站点
  - 不要用 `name` 字段（只改显示名，仍会新建站点）
  - 前端静态资源（app.js、echarts.min.js）被服务端设为 `immutable, max-age=31536000`，更新 JS 必须改文件名或加 `?v=` 版本参数，否则浏览器不拉新

[SQLite 写事务绝不能跨网络 I/O]
- Date: 2026-10-04
- Context: 实现资讯收集的媒体转存 worker 时，登录接口突然 500
- Category: Troubleshooting & Debugging
- Instructions:
  - 症状：`sqlite3.OperationalError: database is locked`，出错语句是无关的 `UPDATE admins SET last_login_at=...`；同时 `/health` 也会卡住
  - 根因：worker 一口气「查 pending → 逐个下载 → 逐个 flush」提交，第 1 项 flush 后的**写事务跨到了第 2 项的下载期间**。下载视频几十秒，写锁被占住
  - WAL 与 `PRAGMA busy_timeout=10000`（见 `backend/app/db.py`）**救不了**这种占用：等待上限只有 10 秒
  - 定式：**短读拿快照 → 结束事务 → 事务外做网络 I/O → 每项一次短事务写回**；参考 `app/info/collector.py` 的 `pending_media_snapshot` / `apply_media_result`
  - 同理，调用上游前先 `db.rollback()` 结束当前事务，拿到响应后再 `db.get()` 取回 ORM 实例继续写

[本机 Python 子进程无法写 %TEMP%]
- Date: 2026-10-04
- Context: 装依赖时 pip / ensurepip 反复失败，pytest 把临时目录落到了仓库里
- Category: Environment Configuration
- Instructions:
  - 本机 PowerShell 能写 `C:\Users\philip\AppData\Local\Temp`，但**Python 进程建文件/建目录会 PermissionError**（listdir 正常，只有创建被拒），因此 `tempfile` 找不到可用临时目录
  - 后果：`tempfile.gettempdir()` 回退成**当前工作目录**；pip 的解包/构建临时目录会落到仓库根（表现为一堆 `pip-metadata-*` / `pip-unpack-*` 目录，需清理）
  - 本机跑 pytest 会在 `backend/` 下留 `pytest-of-philip/`，属正常回退产物，可直接删
  - 绕过：给需要临时目录的命令显式指定一个**工作区内**的可写目录（`$env:TEMP` 指向工作区子目录），POSIX 侧无此问题

[MCP 能力写入口以 Key 白名单为信任边界，不默认加防滥用审查]
- Date: 2026-10-09
- Context: 设计资讯上报能力（MCP+REST 写入口）时评估是否加内容审查/防滥用
- Instructions:
  - 能调用能力写接口的 Agent 都持有管理员签发的 MCP Key，视为可信来源
  - 对外能力写入口默认不做内容审查/防滥用校验；护栏用 Key 白名单 + 限流 + 总开关即可
  - 同源存储型 XSS 等技术性媒体校验（类型白名单、解码校验）仍需保留，与是否信任上传者无关
