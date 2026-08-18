<div align="center">
  <h1>中转台</h1>
  <p>自建 LLM 中转站：对外提供 OpenAI / Anthropic 协议，把请求转到 OpenCode Go、Grok（xAI OAuth）或 DeepSeek</p>
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
- **多上游账号**: OpenCode Go、Grok（xAI OAuth）、DeepSeek，以及通用 OpenAI / 官方 Anthropic
- **管理后台**: 探测上游是否可用、查询额度、拉取模型列表
- **一键导入**: 分享页按 Key 查询归属，支持 CC Switch 导入

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
| `XAI_OAUTH_CLIENT_ID` | Grok OAuth 客户端，可覆盖默认值 |


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

## 生产部署

```bash
cp .env.example .env
cp docker-compose.yml.example docker-compose.yml
docker compose up --build
```

打开 http://127.0.0.1:8000

镜像会先构建前端 `dist`，再由 FastAPI 同源托管页面和接口。

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
│       ├── pages/           # 概览、账号、Key、记录审计
│       └── lib/             # API 封装
├── docs/                    # 设计文档
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
