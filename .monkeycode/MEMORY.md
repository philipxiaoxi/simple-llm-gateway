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

[GitHub 凭据走 unix socket]
- Date: 2026-09-01
- Context: 推送并创建 PR 时，明确要求走 unix socket 取 GitHub 凭据，用 x-access-token 推送
- Category: Workflow & Collaboration
- Instructions:
  - 取凭据统一走 unix socket：`/tmp/codingmatrix-git-credential.sock`，请求 `GET /git-credential?protocol=https&host=github.com`
  - 推送时用户名固定为 `x-access-token`，密码用 socket 返回的 token
  - 不要走 `gh auth git-credential` 或 helper 缓存，过期 token 会 401
  - 回复与日志中不得展示 token 明文，用 `sed -E 's/(basic )[A-Za-z0-9=]+/\1<redacted>/'` 之类方式打码
  - 完整操作步骤（2026-09-02 实测验证）：
    1. 用 Python 的 `http.client.HTTPConnection` 子类连接 unix socket（重写 `connect()` 用 `socket.socket(AF_UNIX)`），请求 `GET /git-credential?protocol=https&host=github.com`
    2. 解析响应：优先按 JSON 取 `token`/`password`/`access_token`，失败则遍历行找 `password=`/`token=` 前缀
    3. 生成认证值：`base64("x-access-token:" + TOKEN)`，拼成 `basic <base64>`
    4. 若通过环境文件传递认证值，必须用 `shlex.quote()` 包裹，否则 `basic` 与 base64 之间的空格会被 shell 当成命令分隔符，导致 token 丢失
    5. 推送命令：`git -c credential.helper= -c "http.https://github.com/.extraheader=AUTHORIZATION: $AUTH_HEADER" push origin HEAD:<branch>`
    6. 完成后删除临时环境文件
  - 常见失败诊断：若提示 `could not read Username`，说明 extraheader 值没传进去（检查环境文件空格/引号问题）；若 401 说明 token 过期，重新从 socket 取
  - 回复中不得展示 token 明文
