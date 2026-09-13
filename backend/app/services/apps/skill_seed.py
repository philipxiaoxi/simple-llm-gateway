from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Skill
from app.services.skills import ParsedSkill, persist_parsed_skills, replace_skill_with_parsed

OCR_SKILL_MD = """---
name: gateway-app-ocr
description: >-
  通过 AI一体化服务平台应用中心调用图片 OCR。适用于识别图片文字、截图转文字、票据/证件 OCR。
  优先用 MCP 工具 app_ocr_recognize；也可用 REST multipart 上传。
---

# 网关应用 · 图片 OCR

## 前置条件

1. 网关管理后台「应用中心」已启用 **图片 OCR**
2. 已绑定支持视觉/多模态的上游账号与模型
3. 持有有效 **API Key**（与调用 `/v1/chat/completions` 相同）

## 方式 A：MCP（推荐）

连接：

```json
{
  "mcpServers": {
    "gateway-app-center": {
      "url": "<GATEWAY_BASE>/mcp",
      "headers": { "Authorization": "Bearer <API_KEY>" }
    }
  }
}
```

调用工具 `app_ocr_recognize`：

```json
{
  "name": "app_ocr_recognize",
  "arguments": {
    "image_base64": "<base64 or data URL>",
    "filename": "shot.png",
    "mime_type": "image/png"
  }
}
```

返回 JSON 字段：`text`、`model`、`ms`。

## 方式 B：REST

```bash
curl -sS -X POST \\
  -H "Authorization: Bearer <API_KEY>" \\
  -F "file=@/path/to/image.png" \\
  "<GATEWAY_BASE>/api/apps/ocr/recognize"
```

## 排错

- 401：API Key 无效或停用
- 403：应用未启用
- 400「请先绑定上游账号」：管理员在应用中心配置绑定
"""

STATIC_SKILL_MD = """---
name: gateway-app-static-deploy
description: >-
  通过 AI一体化服务平台应用中心部署静态前端页面。适用于落地页、简易站点、demo 预览。
  提供 MCP 工具创建站点、上传 zip/文件、列出与删除；公开访问路径为 /a/{slug}/。
---

# 网关应用 · 静态站点部署

## 前置条件

1. 应用中心已启用 **静态站点部署**
2. 有效 API Key

## MCP 工具

连接同 OCR：`<GATEWAY_BASE>/mcp` + `Authorization: Bearer <API_KEY>`

| 工具 | 作用 |
|------|------|
| `app_static_list_sites` | 列出站点与 public_url |
| `app_static_create_site` | 创建站点（name + slug） |
| `app_static_deploy_files` | 部署 zip_base64 或 files[] |
| `app_static_delete_site` | 删除站点 |

### 创建

```json
{
  "name": "app_static_create_site",
  "arguments": { "name": "Demo", "slug": "demo", "description": "落地页" }
}
```

### 部署 zip

```json
{
  "name": "app_static_deploy_files",
  "arguments": {
    "site_id": 1,
    "zip_base64": "<zip file base64>"
  }
}
```

### 部署单文件

```json
{
  "name": "app_static_deploy_files",
  "arguments": {
    "site_id": 1,
    "files": [
      {
        "path": "index.html",
        "content_base64": "<html base64>"
      }
    ]
  }
}
```

公开地址：`<GATEWAY_BASE>/a/{slug}/`

## REST 备选

```bash
# 创建
curl -sS -X POST -H "Authorization: Bearer <API_KEY>" -H "Content-Type: application/json" \\
  -d '{"name":"Demo","slug":"demo"}' \\
  "<GATEWAY_BASE>/api/apps/static-deploy/sites"

# 上传 zip
curl -sS -X POST -H "Authorization: Bearer <API_KEY>" \\
  -F "file=@site.zip" \\
  "<GATEWAY_BASE>/api/apps/static-deploy/sites/1/upload"
```
"""

MCP_SKILL_MD = """---
name: gateway-app-center-mcp
description: >-
  连接 AI一体化服务平台应用中心 MCP，发现并调用已启用应用（OCR、静态部署等）。
  当用户要求用网关能力识别图片、部署静态页、或询问应用中心 MCP 怎么接时使用。
---

# 网关应用中心 MCP

## 连接

```json
{
  "mcpServers": {
    "gateway-app-center": {
      "url": "<GATEWAY_BASE>/mcp",
      "headers": {
        "Authorization": "Bearer <API_KEY>"
      }
    }
  }
}
```

也可用 `<GATEWAY_BASE>/api/mcp`。

## 鉴权

与 LLM 网关相同：`Authorization: Bearer sk-...` 或请求头 `x-api-key`。

## 协议

HTTP JSON-RPC 2.0：

1. `initialize`
2. `tools/list`
3. `tools/call`（`params.name` + `params.arguments`）

探测：

```bash
curl -sS -H "Authorization: Bearer <API_KEY>" "<GATEWAY_BASE>/mcp"
curl -sS -H "Authorization: Bearer <API_KEY>" "<GATEWAY_BASE>/api/apps/integration"
```

## 内置工具（随应用启停变化）

- `app_list` — 已启用应用
- `app_ocr_recognize` — 图片 OCR
- `app_static_list_sites` / `app_static_create_site` / `app_static_deploy_files` / `app_static_delete_site`

管理员在后台启用应用并完成 OCR 模型绑定后，对应工具才会出现在 `tools/list`。

## REST 等价

- `GET /api/apps/tools`
- `POST /api/apps/tools/call`  body: `{"name":"...","arguments":{...}}`
"""


def _parsed(slug: str, name: str, description: str, body: str) -> ParsedSkill:
    return ParsedSkill(
        slug=slug,
        name=name,
        description=description,
        category="Agent工具与平台",
        platforms=["claude", "cursor", "codex"],
        license=None,
        version="1.0.0",
        author="gateway",
        source_name="app-center-builtin",
        skill_md=body,
        files={"SKILL.md": body.encode("utf-8")},
    )


def seed_app_center_skills(db: Session) -> list[str]:
    """幂等写入/更新应用中心相关 Skills，供 Agent 安装调用。"""
    specs = [
        _parsed(
            "gateway-app-center-mcp",
            "网关应用中心 MCP",
            "连接应用中心 MCP，调用 OCR 与静态部署等已启用应用。",
            MCP_SKILL_MD,
        ),
        _parsed(
            "gateway-app-ocr",
            "网关应用 · 图片 OCR",
            "通过 MCP/REST 调用应用中心图片 OCR。",
            OCR_SKILL_MD,
        ),
        _parsed(
            "gateway-app-static-deploy",
            "网关应用 · 静态站点部署",
            "通过 MCP/REST 部署静态前端到 /a/{slug}/。",
            STATIC_SKILL_MD,
        ),
    ]
    touched: list[str] = []
    for parsed in specs:
        existing = db.scalar(select(Skill).where(Skill.slug == parsed.slug))
        if existing is None:
            persist_parsed_skills(db, [parsed])
            touched.append(parsed.slug)
        else:
            # 仅覆盖内置来源，避免误伤用户同名 skill
            if (existing.source_name or "").startswith("app-center"):
                replace_skill_with_parsed(db, existing, parsed)
                touched.append(parsed.slug)
    db.flush()
    return touched
