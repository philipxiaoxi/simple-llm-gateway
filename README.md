<div align="center">
  <h1>AI一体化服务平台</h1>
  <p>自建AI一体化服务平台：对外提供 OpenAI / Anthropic 协议，把请求转到 OpenCode Go、Grok（xAI OAuth）、DeepSeek 或智谱（GLM）；后台可管理本地 Agent Skills</p>
  <p>
    <a href="#-功能特性">功能特性</a> •
    <a href="#快速开始">快速开始</a> •
    <a href="#客户端怎么填">客户端</a> •
    <a href="#技术栈">技术栈</a> •
    <a href="#项目结构">项目结构</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=fff" alt="FastAPI">
    <img src="https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=000" alt="React">
    <img src="https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=fff" alt="Vite">
    <img src="https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=fff" alt="SQLite">
    <img src="https://img.shields.io/badge/LiteLLM-1a1a2e?logo=openai&logoColor=fff" alt="LiteLLM">
    <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT">
  </p>
</div>

## ✨ 功能特性

- **双协议网关**: 同时提供 OpenAI 与 Anthropic 接口，一份 Key 两种填法
- **多上游账号**: OpenCode Go、Grok（xAI OAuth）、DeepSeek、智谱（GLM 按量计费，支持额度查询），以及通用 OpenAI / 官方 Anthropic
- **管理后台**: 探测上游是否可用、查询额度、拉取模型列表
- **一键导入**: 分享页按 Key 查询归属，支持 CC Switch 导入
- **网关代理**: 可将受限网络中的固定上游地址安全地反向接入 Gateway
- **Skills 仓库**: 上传符合 `SKILL.md` 规范的目录 / zip / tar，按分类浏览、编辑元数据并下载
- **MCP 广场**: 独立 MCP Key、能力白名单、知识库（文本分块 + 向量/全文检索），REST `/v1/capabilities` 与 MCP `/mcp` 双协议
- **手机语音输入**: 手机按住说话 → 阿里云实时识别边说边出字 → AI 纠错原地替换 → 自动填进电脑输入框，全链路日志可查

## 环境要求

- Python 3.11
- Node 20+
- 可选：Docker

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 必须改 APP_SECRET_KEY 和 ADMIN_PASSWORD，沿用示例值会拒绝启动
```

| 变量 | 说明 |
|------|------|
| `APP_SECRET_KEY` | JWT 和密钥加密主密钥。丢了旧 Key 全部解不开 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 仅空库第一次种管理员 |
| `DATABASE_PATH` | SQLite 路径，默认 `data/gateway.db` |
| `APP_BASE_URL` | 管理端根地址；自定义 OAuth 应用回调成功后会回到这里 |
| `REQUEST_TIMEOUT_SECONDS` | 默认 120 |
| `QUOTA_REFRESH_INTERVAL_SECONDS` | 上游额度自动刷新间隔，默认 3600（1 小时） |
| `QUOTA_TIMEOUT_SECONDS` | 额度查询超时，默认 20；连接阶段最多 5 秒，上游不可达时快速失败 |
| `XAI_OAUTH_CLIENT_ID` | Grok OAuth 客户端，可覆盖默认值 |
| `SKILLS_PATH` | Skills 文件目录。默认与数据库同级的 `skills/`（例如 `data/skills`） |


### 2. 启动后端

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
./scripts/dev-backend.sh
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:5173 ，用 `.env` 里的管理员账号登录。

### 4. 迁移旧 API Key 绑定

多账号绑定功能上线前，旧版本只在 `api_keys.account_id` 中保存一个上游账号。升级后可执行：

```bash
./.venv/bin/python scripts/migrate_key_accounts.py
```

脚本会把尚未迁移的旧绑定写入 `api_key_accounts`，逐条输出 Key 和账号名称，重复执行不会重复写入。旧版本没有保存其它账号绑定，因此无法自动恢复历史上的多个账号；需要在后台 API Key 页面手动编辑并添加其它账号。

## 客户端怎么填

把下面的 `https://你的站` 换成网关地址，Key 在后台「API Key」页生成。

```text
# OpenCode / Cursor / 普通 OpenAI SDK
base_url = https://你的站/v1
api_key  = sk-xxx

# Claude Code
ANTHROPIC_BASE_URL=https://你的站
ANTHROPIC_AUTH_TOKEN=sk-xxx
# 或
ANTHROPIC_BASE_URL=https://你的站/anthropic

# Grok CLI
base_url = https://你的站/v1
```

创建 Key 时必须选一个上游账号，这把 Key 只会打到那个账号。

## 上游模型列表

账号卡片里点「获取模型」从上游 `/models` 拉取并入库。

- 上游 `/models` 经常漏掉向量等模型，可用「添加模型」手工补录，列表里标记为「自定义」。
- 手工补录的模型和手改过的能力（窗口、思考、模态）在下次刷新时都会保留。
- 手工添加的模型可在能力弹窗里删除；上游模型用启用开关隐藏，刷新时会按上游状态更新。

## MCP 广场与知识库

管理后台「MCP 广场」提供能力目录、知识库、**独立 MCP Key**（与聊天 `sk-` 分离）和调用记录。

1. 在「MCP Key」创建密钥，勾选 `knowledge` 等能力；密钥前缀 `mcp-`，创建后可在列表点「查看」再次获取并复制完整密钥。
2. 在「知识库」建库并粘贴/上传纯文本，或选择整个目录批量入库。文本文件直接入库；Word、PDF、Excel、PPT、HTML 先转成 Markdown 再入库。图片和压缩包仍跳过。每个文件一个采集任务，系统分块后写入 SQLite FTS5 + Chroma。
3. 向量化默认走 OpenAI 兼容 embeddings（见 `.env` 的 `MCP_EMBEDDING_*`）；未配置时使用本地 Fake embedding（仅适合开发）。
4. 访问范围：`public` 对所有 Key 可见，`restricted` 只对白名单内的 MCP Key 可见，`private` 仅管理端可见。
5. 更换 embedding 模型或维度会把已有文档标记为失效（`stale`），用「重建向量」按需或全量重建，避免新旧向量混用。
6. 每日自动清理已结束的采集任务、调用日志和残留原文，天数见 `MCP_*_RETENTION_DAYS`。
7. 「调用记录」按能力、Key、成功/失败和关键词筛选，支持分页，错误信息完整展示。
8. 下游调用：

```bash
# REST
curl -s https://你的站/v1/capabilities/knowledge/search \
  -H "Authorization: Bearer mcp-xxx" \
  -H "Content-Type: application/json" \
  -d '{"kb_id":"<id>","query":"关键词","mode":"hybrid","top_k":5}'

# MCP Streamable HTTP
# URL: https://你的站/mcp
# Header: Authorization: Bearer mcp-xxx
# tools: knowledge_list, knowledge_search
#        docparse_convert, docparse_job, docparse_result
#        site_deploy, site_list, site_status, site_rollback, site_delete, site_access
```

文档转换在 MCP 广场「文档转 Markdown」页上传预览。MCP Key 勾选 `docparse` 后，可走 REST `POST /v1/capabilities/docparse/jobs`（multipart 字段 `file`）或 MCP 工具 `docparse_convert`。Docker 部署随镜像安装 `markitdown`，文件落在数据卷 `/data/docparse`。旧版 `.doc/.xls/.ppt` 需要镜像外另行提供 LibreOffice。

站点部署在 MCP 广场「站点部署」页使用：上传前端构建产物 `.zip`，系统安全解包为不可变版本并生成 `/sites/{slug}/` 预览地址，支持一键回滚与访问令牌保护。重复内容按归一去重摘要识别并复用版本。管理端与 REST（`POST /v1/sites`）为异步部署，返回 `unpacking` 状态后轮询版本接口；MCP Key 勾选 `site` 后可用 `site_deploy`、`site_list`、`site_status`、`site_rollback`、`site_delete`、`site_access`。文件落在数据卷 `/data/sites`，配置项见 `.env` 的 `SITE_*`。

托管会改写 HTML/CSS 里以 `/` 开头的根绝对资源路径（如 Vite 默认产物的 `/assets/index-*.js`），并在 `<head>` 注入 `<base>`，因此 `base: '/'` 的构建产物无需改动即可预览；`base: './'` 同样兼容。平台 Service Worker 的导航兜底已排除 `/sites/`，已安装 PWA 的浏览器不会把预览地址劫持成管理端外壳。

「接入说明」页是按 MCP Key 生成的**接入中心**：选择 Key 后只展示其已授权能力，可一键复制适配 MCP 或「Skill + REST」的 AI 提示词、通用 MCP 客户端 JSON 与密钥。MCP Key 列表每行有「接入」按钮直达对应 Key。

`mode=hybrid` 用 RRF 融合全文与向量结果（无需归一化两路分数），在 embedding 不可用时自动降级全文并返回 `degraded=true`；`mode=vector` 失败则明确报错。搜索支持 `kb_id` 单库或 `kb_ids` 跨库。

## Skills 管理

后台「Skills」页是本地 Agent Skills 仓库，参考 [SkillHot](https://skillhot.savs-ai.com/) 的分类与卡片浏览，但文件保存在本机，不爬取 GitHub。

每个 Skill 必须是一个目录，根目录有 `SKILL.md`，并带 YAML frontmatter（至少 `name`、`description`）。支持：

- 上传单个 zip / tar / tar.gz
- 上传整个目录（浏览器会带上相对路径）
- 一次导入合集压缩包里的多个 Skill
- 按分类筛选、搜索、编辑名称/分类/描述
- 下载整个 Skill 为 zip，或单独下载某个文件
- 复制 Claude Code 安装指令：生成 5 分钟有效的下载链接，连同安装步骤一起复制给 AI 一键安装
- 复制上传指令：生成 5 分钟有效的上传链接，交给 AI 打包本机各 Agent skills 目录并上传；入库名称自动加 `【ai】-时间戳` 前缀
- Skills 组合：将已入库 Skill 编成组合包，支持成员增删、整包 zip 下载（每个 Skill 一个顶层目录）与复制安装指令

文件默认写在与数据库同级的 `skills/`（Docker 下即 `/data/skills`，已被 `./data` 卷覆盖）。可用 `SKILLS_PATH` 改路径。单次上传上限 20MB，解压后 40MB / 400 个文件。

## 手机语音输入 → 电脑输入框

把手机当麦克风：按住说话 → 阿里云实时语音识别边说边出字 → AI 纠错 → 自动填进电脑的输入框。
设计文档见 [`docs/voice-input/`](docs/voice-input/)。

### 1. 配置

`.env` 里加两项（其余可选，见 `.env.example`）：

```bash
ALIYUN_DASHSCOPE_API_KEY=sk-ws-xxxxxxxx        # 阿里云百炼实时语音识别 Key，只存服务端
VOICE_DESKTOP_TOKEN=一段随机的长字符串          # 桌面客户端共享令牌
```

自检阿里云连通性（不产生真实识别内容）：

```bash
ALIYUN_DASHSCOPE_API_KEY=sk-ws-xxx ./.venv/bin/python scripts/voice_asr_probe.py
# 加 --audio 你的16k单声道.wav 可验证真实转写效果
```

### 2. 后台建房间

管理后台 →「语音房」→ 新建房间。可配置：

- **识别模型**：默认 `qwen-audio-3.0-asr-flash-streaming`，可换 `fun-asr-realtime` / `paraformer-realtime-v2`
- **自动删除语气词**：默认开。实测关掉后 ASR 一个「嗯/那个/就是说」都不会删
- **AI 纠错档位**：`关闭` / `只纠错`（默认）/ `允许改写`。纠错用本站的上游账号与模型，可先点「试跑」看效果
- **房间口令**：可选，手机加入时需要

### 3. 手机加入

手机浏览器打开 `https://你的站/voice/join`，输入房间码（或扫码/点带 `?code=` 的链接）。

> **麦克风要求安全上下文**：必须 HTTPS，或本机 `localhost` / `127.0.0.1` 访问。HTTP 的局域网 IP 打不开麦克风。

按住底部按钮说话，实时出字；上滑可取消；松手后 AI 纠错会在原句上**原地替换**。

### 4. 电脑端

```bash
cd voice-desktop
npm install
node src/index.js --init     # 交互式生成 ~/.llm-gateway-voice.json
node src/index.js
```

在后台房间页点「复制电脑端配置」，粘贴到 `~/.llm-gateway-voice.json` 即可。

首次运行需要给终端**辅助功能权限**：系统设置 → 隐私与安全性 → 辅助功能 → 勾选你的终端，
并允许「想要控制 System Events」的弹窗。**默认不注入**，按 `i` 进入插入模式后才会写入输入框
（防止误注入）。按 `s` 看状态，`q` 退出。

### 5. 日志

后台房间页有四个 Tab：实时 / 配置 / 识别日志 / 事件时间线。每次说话都会记录：

| 记录内容 | 位置 |
|---|---|
| ASR 原始文本 | 识别日志 `原文` 列、事件 `识别完成` |
| AI 纠错后文本 | 识别日志 `AI 纠错后` 列、事件 `纠错完成` |
| 纠错状态与耗时 | 识别日志 `状态`/`耗时` 列（含「被判定过度改写」） |
| 下发给几台电脑 | 识别日志 `下发` 列、事件 `下发文本` |
| 电脑是否已上屏 | 识别日志 `回执` 列、事件 `电脑已上屏` |
| 加入/离开、录音起止、放弃替换、注入失败 | 事件时间线 |

### 排错

| 现象 | 原因 |
|---|---|
| 手机页面提示「需要 HTTPS」 | 麦克风只在安全上下文可用 |
| 手机提示「识别服务不可用」 | 服务端没配 `ALIYUN_DASHSCOPE_API_KEY` 或 Key 无效 |
| 文字记录了但电脑没上屏 | 电脑端没连上，或没按 `i` 进入插入模式；看事件时间线的 `放弃替换`/`注入失败` |
| 纠错一直「无需修改」 | 正常。ASR 开了去语气词后原文往往已经干净，这一跳只在真有错字时才动手 |
| 纠错报「token 预算被推理过程耗尽」 | 换了推理型模型，调大 `VOICE_POLISH_MAX_TOKENS` 或换非推理模型 |
| 电脑端报「发生权限违例 (-10004)」 | 终端没有辅助功能/自动化权限，见上面第 4 节的授权步骤；授权后必须**重启终端** |
| 极短句（如「你好你好」）没被纠错 | 过度改写保护：只能改 5 个字时删 2 个字就超过 40% 编辑距离阈值，系统选择保留原文 |

> 上游模型怎么选：推理型模型（如 `deepseek-v4-*`）会先把 token 花在思考上，长句纠错约 1~2.5s；
> 想要更快可以换成非推理的小模型。后台房间配置页的「试跑纠错」会显示输入、输出和耗时，方便对比。

## 生产部署

```bash
cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
docker compose up --build
```

打开 http://127.0.0.1:8000

镜像会先构建前端 `dist`，再由 FastAPI 同源托管页面和接口。

MCP 广场与知识库相关的容器注意事项：

- 数据都落在 `./data` 卷：`gateway.db`、向量库 `/data/chroma`、采集任务原文 `/data/knowledge_jobs`。`MCP_CHROMA_PATH` 留空即用 `/data/chroma`；配置成相对路径时按数据库所在目录解析，不会落到容器 WORKDIR。
- Chroma 的匿名遥测已在代码里关闭（`PersistentClient(settings=Settings(anonymized_telemetry=False))`），容器不会外呼 posthog。
- 向量检索需要真实 embeddings，在 `.env` 配置 `MCP_EMBEDDING_BASE_URL` / `MCP_EMBEDDING_API_KEY` / `MCP_EMBEDDING_MODEL`；未配置会退回本地 Fake embedding，只适合开发。
- 采集任务的 worker 与 Chroma 都是进程内组件，保持单进程运行（默认 `CMD` 不带 `--workers`）；加 `--workers` 会导致多套 worker 抢任务并并发访问同一份 Chroma。
- MCP 客户端地址用 `/mcp`。`/mcp` 与 `/mcp/` 都会进入 MCP 服务；前置反向代理时需把这两条路径一并转发到后端。
- 升级时 `init_db()` 会自动给已有的 `gateway.db` 建表和加列，先备份 `./data` 即可。

> 语音输入用了 WebSocket。反向代理除了 `/agent/connect`，还要放行 `/api/voice/**` 与
> `/voice/**` 的 `Upgrade` / `Connection` 头，否则手机和电脑都连不上房间。

## 网关代理

Gateway 直接承载网关代理的 WSS 连接，不需要单独部署 Relay 容器。为网关设置随机的 `LOCAL_AGENT_TOKEN`，然后在能访问本地上游的机器上执行：

```bash
cd agent
npm install
cp config.example.json config.json
# 编辑 config.json：填写 Gateway 的 wss 地址、相同的 token 与固定上游 route
npm start
```

网关代理只会访问 `config.json` 中声明的 `targetBaseUrl`。在管理后台创建账号时，将上游地址填为 `http://127.0.0.1:8000/r/<route-id>/v1`；Docker 部署则填 `http://gateway:8000/r/<route-id>/v1`。账号的上游 Key 仍只保存在 Gateway。

## 项目结构

```
llm-gateway/
├── backend/                 # FastAPI 网关 + 管理 API
│   ├── app/
│   │   ├── providers/       # 上游供应商
│   │   ├── routers/         # 管理、代理、分享、OAuth
│   │   └── services/        # 转发、探测、额度、凭证
│   └── tests/
├── frontend/                # Vite + React 管理端
│   └── src/
│       ├── pages/           # 概览、账号、Key、Skills、记录审计
│       └── lib/             # API 封装
├── docs/                    # 设计文档（含 voice-input/ 语音输入方案）
├── voice-desktop/           # 电脑端注入客户端（Node.js）
├── agent/                   # 本地网络常驻 Node.js Agent
├── scripts/                 # 开发启动脚本
├── Dockerfile
└── docker-compose.yml.example
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端框架 | React 19 + Vite |
| 样式 | Tailwind CSS 4 |
| 数据请求 | TanStack Query |
| 后端框架 | FastAPI |
| 数据库 | SQLite (SQLAlchemy 2) |
| 协议转换 | LiteLLM |
| 认证 | JWT + bcrypt |
| 部署 | Docker Compose |

## 测试

```bash
source .venv/bin/activate
cd backend && pytest -q
```

## 许可证

[MIT](LICENSE)
