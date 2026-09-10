# voice-desktop · 手机语音输入 → 电脑输入框

常驻在电脑上的小客户端。手机网页录下语音 → 服务端（阿里云实时语音识别 + 大模型纠错）→
WebSocket 把文本推到这里 → 它把文本**填进你当前光标所在的输入框**（微信 / 飞书 / VSCode /
Chrome 都行），并在服务端发来「纠错后的文本」时把刚填进去的那段**原地替换**掉。

- 不需要装任何原生模块（**不用** node-gyp / nut-js / robotjs）
- 唯一运行时依赖是 `ws`
- 注入方式：`pbcopy` 写剪贴板 + `osascript` 发 `Cmd+V`，中文 / emoji / 跨 App 都稳

> 设计与协议见 `docs/voice-input/桌面客户端.md`、`docs/voice-input/协议参考.md` §2.4。

---

## 1. 环境要求

| 项 | 要求 |
|---|---|
| 系统 | macOS（v1 只支持 macOS；Windows / Linux 是 P2） |
| Node | **>= 20**（ESM 项目） |
| 权限 | 辅助功能权限 + 自动化权限（见第 3 节） |

---

## 2. 安装与配置

### 2.1 安装

```bash
cd voice-desktop
npm install
```

> 如果 npm 报 `EPERM … ~/.npm/_cacache`（全局缓存目录权限坏了），换一个本地缓存即可：
> `npm install --cache ./.npm-cache`。也可以直接从仓库里已有的 `agent/node_modules/ws`
> 复制到 `voice-desktop/node_modules/ws`（唯一依赖就是它）。

### 2.2 生成配置（推荐）

```bash
node src/index.js --init
```

会在 `~/.llm-gateway-voice.json` 生成配置（已存在则合并），并自动写入一个固定的
`clientUid`（形如 `dc_xxxxxxxx-…`，重连后仍是同一台设备）。

也可以手动复制模板：

```bash
cp config.example.json ~/.llm-gateway-voice.json
```

### 2.3 配置项

| 字段 | 默认值 | 说明 |
|---|---|---|
| `serverUrl` | — | **完整**的 WebSocket 地址，如 `wss://你的站/api/voice/desktop/connect` |
| `roomId` | — | 房间 ID（后台房间里那串 `a1b2c3d4`） |
| `token` | — | 房间令牌（30 天有效）或全局 `VOICE_DESKTOP_TOKEN` |
| `clientUid` | 自动生成 | 设备 ID，留空会自动生成 `dc_<uuid>` 并写回 |
| `name` | 主机名 | 手机上显示的设备名 |
| `insertMode` | `false` | 启动时是否打开插入模式（**默认关**，见第 5 节） |
| `clipboardRestore` | `true` | 注入后 300ms 还原你的剪贴板 |
| `replaceEnabled` | `true` | 是否允许「原地替换」（关掉就只追加） |
| `commitDelayMs` | `600` | 停顿窗口：距上次上屏不足这个时间就先不替换 |
| `pollIntervalMs` | `300` | 前台 App / 光标巡检间隔 |
| `logFile` | `~/.llm-gateway-voice.log` | 日志文件（按天轮转，保留 7 天） |

**配置优先级**：命令行参数 > 环境变量 > 配置文件 > 默认值。

支持的环境变量：`VOICE_SERVER_URL`、`VOICE_ROOM_ID`、`VOICE_DESKTOP_TOKEN`
（另外 `VOICE_INSECURE_TLS=1` 可跳过自签证书校验，仅用于自建测试环境）。

---

## 3. 授予辅助功能权限（重要，不做则注入无声失败）

macOS 会**静默忽略**没有权限的按键，所以务必：

1. 打开 **系统设置 → 隐私与安全性 → 辅助功能**
2. 勾选运行本程序的宿主程序：`Terminal` / `iTerm` / `VS Code`（用哪个跑就勾哪个）
3. 首次运行还会弹「**Terminal 想要控制 System Events**」→ 点**允许**
   （对应 系统设置 → 隐私与安全性 → 自动化）
4. **授权后重启本程序**（macOS 不会给已在运行的进程补权限）

启动时会自动做一次无害自检，失败会直接打印中文指引，不会静默失败。

---

## 4. 启动与用法

```bash
# 方式一：配置文件
node src/index.js

# 方式二：全部用参数
node src/index.js --server-url wss://你的站/api/voice/desktop/connect --room a1b2c3d4 --token "<令牌>"

# 方式三：环境变量
VOICE_SERVER_URL=wss://… VOICE_ROOM_ID=a1b2c3d4 VOICE_DESKTOP_TOKEN=… node src/index.js

# 启动即打开插入模式
node src/index.js --insert-mode
```

启动后是常驻进程，在**终端里按单键**（不需要回车）：

| 按键 | 作用 |
|---|---|
| `i` | 切换插入模式 |
| `s` | 打印状态（连接 / 房间 / 账本 / 最近的段） |
| `c` | 清空段落账本（期望光标位置归零） |
| `r` | 立即重连（改了令牌后用） |
| `q` | 退出（`Ctrl+C` 同效） |

### 典型使用流程（重要）

```
1. 把光标点进目标输入框（微信 / 飞书 / VSCode 编辑区 …）——先别急着走开
2. 回到终端按 i 打开插入模式（会打印「插入模式：开启」）
3. 再点回目标输入框，手指按住手机上的说话按钮
   → 电脑上开始实时出字
4. 说完松手：服务端出纠错稿，刚才那段被原地替换成书面文本
5. 不用了就在终端按 i 关掉插入模式
```

**为什么默认关着插入模式？** 避免把语音内容误注入到你正在写的东西里。
只有你明确按了 `i`，它才会往当前输入框写字。

---

## 5. 工作原理（一句话版）

```
手机按住说话 ──音频──► 服务端 ──ASR──► segment.snapshot(rev0/1/2) ──WS──► 本客户端
                                                                          │
                          ① 新段落 → 剪贴板 + Cmd+V 整段粘贴               │
                          ② 文本变长 → 只追加新增的尾巴                    │
                          ③ 中间被修正 → Shift+← 选中旧尾巴 + 粘贴（原子替换）│
                          ④ 收到纠错终稿(rev2) → 落定为 committed，之后只追加 │
                                                                          ▼
                                                      回执 segment.ack / segment.abandoned
```

- **永远是整段快照**：服务端不下发 diff，客户端本地算「共同前缀」再裁剪尾部，
  丢帧/重连/乱序都能自愈。
- **`rev` 是状态档位，不是自增版本号**：`0` 中间结果（同一段会反复下发、文本不断增长）、
  `1` ASR 终稿、`2` 纠错终稿（`final=true` → 标为 committed，只允许正向追加）。
- **三道闸**：任何一次「回改」都要同时满足 配置允许替换 / 处于插入模式 / 房间没人正在录音 /
  距上次上屏超过停顿窗口 / 光标位置符合预期。任一不满足就**只追加、绝不回改**，
  并上报 `segment.abandoned`（原因：`cursor_mismatch` / `user_typed` / `timeout` /
  `mode_off` / `disabled` / `room_busy`）。
- **落定即冻结**：段落一旦 committed、或后面又接了新段落，就永远不再回改。

---

## 6. 排错

| 现象 | 原因与处理 |
|---|---|
| 手机在说，电脑**完全没反应** | 看终端是否 `已注册`；没有就是 `serverUrl` / `roomId` / `token` 不对（关闭码 1008 会直接提示） |
| 终端显示 `[待插入] …` 但没上屏 | 插入模式没开，按 `i` |
| 有日志但没有字进输入框 | ①辅助功能权限没给（见第 3 节）②光标不在输入框里（先点一下输入框）③目标 App 是密码框（macOS 屏蔽注入，会报 `secure_input`） |
| 只有追加、没有被替换 | 正常保护：房间还在录音 / 离上次上屏太近（停顿窗口）/ 光标被移动过 / 段落已落定。日志里会写明原因 |
| 日志刷 `cursor_mismatch` | 输入框在开始注入前**不是空的**（本方案假定从空输入框开始记位置）。可以按 `c` 清空账本重新对齐 |
| 中文变成乱码 | 极少数 App 的粘贴不接受 UTF-8，请反馈具体 App |
| 提示「未获得辅助功能权限」 | 按提示勾选 Terminal → 重启本程序 |
| 启动时卡一下然后提示「自检超时」 | 首次运行会弹「想要控制 System Events」，点「允许」后重启即可（自检有 3.5s 超时，不会把启动挂死） |
| CPU 占用偏高 | 插入模式下每隔 `pollIntervalMs`（默认 300ms）会用 osascript 查一次前台 App；调大该值即可降低占用 |
| 日志刷 `inject.error: perm_denied` | 权限没给（同第 3 节）；同一段同一原因只上报一次，不会刷屏 |
| `wss` 握手失败 / 证书错误 | 自签证书可临时用 `VOICE_INSECURE_TLS=1` |
| 想看历史 | `~/.llm-gateway-voice.log`（按天轮转，保留 7 天） |

日志格式（带时间戳、中文、按天轮转）：

```
[10:00:01] ✓ 已连接 wss://你的站/api/voice/desktop/connect
[10:00:01] ✓ 已注册 · 房间「书房」· 对端手机 1 台
[10:00:04] ← rev0 「你好我们」 追加「我们」 12ms
[10:00:04] ← rev1 「你好，我们明天见。」 替换尾部「，我们明天见。」 28ms
[10:00:05] ⚠ 闸门拦截（room_busy）→ 只追加 3 字，不回改
```

---

## 7. 开发与测试

```bash
npm test          # node --test，84 个用例（段落算法 + 注入编排 + WS 协议）
npm start         # 等同 node src/index.js
node src/index.js --help
```

```
src/segments.js   共同前缀 / 尾部 diff / 段落账本 / 三道闸（纯函数，可单测）
src/inject.js     注入实现（pbcopy / osascript）+ 剪贴板守卫 + 动作编排（依赖注入，可单测）
src/ws.js         注册、15s 心跳、指数退避重连（1→2→4→8→…30s，±20% 抖动）、按帧去重
src/config.js     配置合并、clientUid 生成与写回、--init 交互
src/logger.js     控制台彩色中文日志 + 文件日志（按天轮转）
src/index.js      入口：主循环、插入模式、单键命令、权限自检
test/             segments.test.js（文档 §9 用例表）/ inject.test.js（假 executor）/ ws.test.js（本地假服务端）
```

测试全程使用**依赖注入的假 executor**，不会真的动键盘、不会碰真实剪贴板。

---

## 8. 与文档的差异（实现说明）

1. **`too_soon` / `room_busy` 采用「延后重试」而不是立刻永久放弃**
   依据 [技术方案.md §3.6](../../docs/voice-input/技术方案.md) 的停顿窗口设计：
   距上次上屏 < `commitDelayMs` 时「推迟到 600ms 后再执行（最多 3s，超时则放弃）」，
   房间正在录音时「暂存到 pending，等录音停止」。若按字面立刻放弃并把段落标记为
   committed，一句话里每次 ASR 修正标点都会锁死这一段，纠错终稿就永远替换不回去了。
   超时后仍会降级为「只追加 + 上报 `segment.abandoned`」（`too_soon` 上报为 `timeout`）。
2. **`(segId, rev)` 去重带上文本内容**
   后端 `voice_session._on_partial` 会对同一段反复下发 `rev=0` 的快照（≥120ms 一次、
   文本不断增长），只用 `(segId, rev)` 去重会把中间结果全部丢掉。
   带上 `text` 后，服务端原样重发的重复帧依旧被幂等丢弃。
3. **只有「仍在光标处的最后一段」允许回改**
   尾部替换算法的物理限制：一旦有新段落接在后面，前一段就无法再回改。
   此时即使文档要求「只追加」，也不能把尾巴追加到别的段落后面（会串行错位），
   因此改为跳过并上报 `segment.abandoned`。
4. **首次连接收到 `recentSegments` 时只登记、不重复上屏**（避免重启客户端后把最近
   5 句历史文本倒进你当前的输入框）；**重连**时才对未见过的段落做补发。
5. `recentSegments` 的字段按后端实际结构归一化：`polishedText || rawText`
   （后端返回的是 `{segId, seq, rev, state, rawText, polishedText, …}`，没有 `text` 字段）。
6. **多补了两处工程保护**（不改变协议）：
   - 所有 `osascript` 调用带超时（自检 3.5s / 常规 5s）。首次运行时系统会弹
     「想要控制 System Events」，若不点允许，原生做法会把启动流程挂死。
   - 整段插入后用实测光标位置**重新基线化** `expectedPos`（每段仅多一次 osascript），
     这样即使目标输入框本来就不为空，「光标不符就放弃替换」这道闸门依然有效。
