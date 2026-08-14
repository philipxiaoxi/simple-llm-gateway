# 中转台

自建 LLM 中转站：对外提供 OpenAI / Anthropic 协议，把请求转到 OpenCode Go、Grok（xAI OAuth）或 DeepSeek。管理员网页可手动探测上游、查询上游额度。

## 要求

- Python 3.11（不要用 3.14，部分依赖没有现成 wheel）
- Node 20+
- 可选：Docker

## 快速启动（开发）

```bash
cp .env.example .env
# 改 APP_SECRET_KEY 和 ADMIN_PASSWORD

python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend --port 8000
```

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:5173 ，用 `.env` 里的管理员账号登录。

## Docker

```bash
cp .env.example .env
docker compose up --build
```

打开 http://127.0.0.1:8000

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

## 环境变量

| 变量 | 说明 |
|------|------|
| `APP_SECRET_KEY` | JWT 和密钥加密主密钥。丢了旧 Key 全部解不开 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 仅空库第一次种管理员 |
| `DATABASE_PATH` | SQLite 路径，默认 `data/gateway.db` |
| `APP_BASE_URL` | Grok OAuth 回调根地址 |
| `REQUEST_TIMEOUT_SECONDS` | 默认 120 |
| `XAI_OAUTH_CLIENT_ID` | Grok OAuth 客户端，可覆盖默认值 |

数据库文件和 `APP_SECRET_KEY` 不要放在同一份备份里。

## 测试

```bash
source .venv/bin/activate
cd backend && pytest -q
```
