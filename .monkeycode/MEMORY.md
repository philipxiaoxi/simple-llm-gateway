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
  - 用 extraheader 推送：`git -c credential.helper= -c http.https://github.com/.extraheader="AUTHORIZATION: basic <base64(x-access-token:TOKEN)>" push origin HEAD:<branch>`
  - 不要走 `gh auth git-credential` 或 helper 缓存，过期 token 会 401
  - 回复中不得展示 token 明文
