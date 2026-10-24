#!/usr/bin/env python3
"""给本地/验收环境灌一批演示数据。

覆盖三类验收场景：

1. Skills：分类 + 若干带 SKILL.md 与资源文件的技能 + 技能组合包；
2. RBAC：只读、技能只读、内容运营、运维四类角色与对应账号；
3. MCP：按目录里已启用的能力创建两个 MCP Key。

脚本幂等：同名分类/角色/账号、同 slug 技能会跳过；重复执行只补齐缺失部分。

用法::

    python3 scripts/seed_acceptance.py
    BASE=http://127.0.0.1:8000 ADMIN_USERNAME=admin ADMIN_PASSWORD=admin12345 python3 scripts/seed_acceptance.py

只依赖标准库；结束时打印各账号的用户名与密码，方便直接登录验收。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE", "http://127.0.0.1:8000").rstrip("/")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin12345")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "accept-2026")


def _request(method: str, path: str, token: str | None = None, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = raw.decode("utf-8", "replace")
        return error.code, body


def _upload(token: str, files: list[tuple[str, bytes]], category: str) -> tuple[int, object]:
    return _multipart(token, "/api/admin/skills/upload", files, category)


def _multipart(token: str, path: str, files: list[tuple[str, bytes]], category: str) -> tuple[int, object]:
    boundary = f"----seed{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for filename, content in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode()
        )
        chunks.append(b"Content-Type: application/octet-stream\r\n\r\n")
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(b'Content-Disposition: form-data; name="category"\r\n\r\n')
    chunks.append(category.encode())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)

    request = urllib.request.Request(BASE + path, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = raw.decode("utf-8", "replace")
        return error.code, body


SKILLS: list[dict] = [
    {
        "name": "前端代码审查",
        "slug": "frontend-code-review",
        "category": "编程开发",
        "description": "审查前端改动并给出结论优先的清单：先列风险，再列未修项，最后附自评分数。",
        "body": "# 前端代码审查\n\n按「结论 → 改动 → 未修/待确认」顺序输出，避免大段复述。",
        "files": {
            "references/checklist.md": "# 检查清单\n\n- 状态管理是否越层\n- 是否存在竞态与内存泄漏\n- 无障碍与键盘可达性\n",
        },
    },
    {
        "name": "接口 Mock 生成器",
        "slug": "api-mock-generator",
        "category": "编程开发",
        "description": "根据 OpenAPI 片段生成可直接运行的 Mock 服务与示例数据，支持分页与错误分支。",
        "body": "# 接口 Mock 生成器\n\n输入 OpenAPI 片段，输出本地 mock server 与样例响应。",
        "files": {"resources/openapi.sample.yaml": "openapi: 3.0.0\ninfo:\n  title: demo\n"},
    },
    {
        "name": "发布说明整理",
        "slug": "git-release-notes",
        "category": "编程开发",
        "description": "把提交记录整理成面向用户的发布说明，按功能、修复、破坏性变更分组。",
        "body": "# 发布说明整理\n\n从 git log 抽取信息，输出三段式发布说明。",
        "files": {},
    },
    {
        "name": "UI 走查清单",
        "slug": "ui-review-checklist",
        "category": "UI设计",
        "description": "对页面做像素级走查：间距、层级、对比度、移动端断点与触控热区。",
        "body": "# UI 走查清单\n\n逐项核对设计稿，输出偏差与修复建议。",
        "files": {"references/tokens.md": "# 设计变量\n\n- 主色 / 强调色\n- 圆角与阴影层级\n"},
    },
    {
        "name": "周报生成",
        "slug": "weekly-report-writer",
        "category": "办公效率",
        "description": "把一周的零散记录归纳成结构化周报，突出结果、风险与下周计划。",
        "body": "# 周报生成\n\n结果优先，风险置顶，计划可量化。",
        "files": {"resources/weekly-template.md": "# 本周结果\n\n# 风险\n\n# 下周计划\n"},
    },
    {
        "name": "Excel 清洗助手",
        "slug": "excel-cleaner",
        "category": "办公效率",
        "description": "识别表头错位、合并单元格与脏数据，给出清洗步骤与校验规则。",
        "body": "# Excel 清洗助手\n\n先做结构体检，再给清洗脚本。",
        "files": {},
    },
    {
        "name": "会议纪要提炼",
        "slug": "meeting-notes-digest",
        "category": "办公效率",
        "description": "把会议逐字稿提炼成决议、待办与责任人，按重要度排序。",
        "body": "# 会议纪要提炼\n\n输出决议、待办、责任人三块。",
        "files": {},
    },
    {
        "name": "数据洞察摘要",
        "slug": "data-insight-summary",
        "category": "数据分析",
        "description": "对指标波动做归因：区分口径变化与真实变化，输出可执行结论。",
        "body": "# 数据洞察摘要\n\n先核对口径，再解释波动。",
        "files": {"references/metrics.md": "# 常用指标\n\n- 活跃 / 留存 / 转化\n"},
    },
    {
        "name": "SQL 性能优化",
        "slug": "sql-optimizer",
        "category": "数据分析",
        "description": "分析慢查询执行计划，给出索引与改写建议，并评估写入放大成本。",
        "body": "# SQL 性能优化\n\n看执行计划，避免全表扫描与隐式类型转换。",
        "files": {},
    },
    {
        "name": "文案润色",
        "slug": "copywriting-rewriter",
        "category": "内容创作",
        "description": "把口语化草稿改写成克制、具体的产品文案，保留原意并统一语气。",
        "body": "# 文案润色\n\n克制、具体、去口号化。",
        "files": {"resources/tone.md": "# 语气准则\n\n- 少用形容词\n- 用动词开头\n"},
    },
]

CATEGORIES = [
    ("UI设计", ["ui", "界面", "视觉", "设计稿"]),
    ("编程开发", ["code", "代码", "编程", "review", "sql", "git"]),
    ("办公效率", ["report", "周报", "excel", "会议", "办公"]),
    ("数据分析", ["data", "数据", "指标", "分析"]),
    ("内容创作", ["copy", "文案", "写作", "内容"]),
]

BUNDLES = [
    {
        "name": "前端开发套件",
        "description": "前端改动从走查到发布说明的一条龙技能。",
        "slugs": ["frontend-code-review", "ui-review-checklist", "git-release-notes"],
    },
    {
        "name": "数据与报告",
        "description": "数据分析、表格清洗与周报汇总。",
        "slugs": ["data-insight-summary", "excel-cleaner", "sql-optimizer", "weekly-report-writer"],
    },
]

ROLES: list[dict] = [
    {
        "name": "验收·只读观察",
        "description": "所有页面的查看权限，用于验证只读角色看不到任何操作入口。",
        "permissions": None,  # 运行时按目录里的全部 view 权限生成
        "users": ["accept-reader"],
    },
    {
        "name": "验收·技能只读",
        "description": "只看 Skills 与组合包，验证下载/复制安装指令/编辑/删除全部隐藏。",
        "permissions": ["skills:view", "skill_bundles:view"],
        "users": ["accept-skills"],
    },
    {
        "name": "验收·内容运营",
        "description": "资讯与生成内容的日常维护，验证内容页按钮可用、系统页仍隐藏。",
        "permissions": [
            "dashboard:view",
            "info:view",
            "info:preview",
            "info:source_create",
            "info:source_update",
            "info:source_delete",
            "info:collect",
            "info:article_create",
            "info:article_delete",
            "info:favorite",
            "info:hidden",
            "info:featured",
            "info:ai_skip",
            "info:ai_settings",
            "info:rescore",
            "skills:view",
            "skills:upload",
            "skills:update",
            "skills:download",
            "skills:analysis",
            "generation:settings",
            "generation:upload",
            "image_gen:view",
            "image_gen:task_create",
            "image_gen:task_retry",
            "image_gen:task_delete",
            "video_gen:view",
            "video_gen:task_create",
            "video_gen:task_retry",
            "video_gen:task_delete",
        ],
        "users": ["accept-content"],
    },
    {
        "name": "验收·运维",
        "description": "账号、Key、记录、代理的日常运维，验证编辑类操作可用。",
        "permissions": [
            "dashboard:view",
            "accounts:view",
            "accounts:create",
            "accounts:update",
            "accounts:probe",
            "accounts:quota",
            "accounts:models_manage",
            "keys:view",
            "keys:create",
            "keys:update",
            "keys:delete",
            "keys:aliases_manage",
            "keys:cc_switch",
            "agents:view",
            "agents:route_bind",
            "logs:view",
            "logs:detail",
            "logs:messages",
            "logs:export",
        ],
        "users": ["accept-ops"],
    },
]


def _skill_markdown(skill: dict) -> bytes:
    frontmatter = [
        "---",
        f"name: {skill['name']}",
        f"slug: {skill['slug']}",
        f'description: "{skill["description"]}"',
        "version: 1.0.0",
        "license: MIT",
        'compatibility: ["Claude Code", "Codex"]',
        "---",
        "",
    ]
    return ("\n".join(frontmatter) + skill["body"] + "\n").encode()


def login(username: str, password: str) -> str | None:
    status, body = _request("POST", "/api/admin/login", payload={"username": username, "password": password})
    if status != 200 or not isinstance(body, dict):
        return None
    return body.get("token")


def view_permissions(token: str) -> list[str]:
    status, catalog = _request("GET", "/api/admin/permission-catalog", token)
    if status != 200 or not isinstance(catalog, list):
        return []
    codes: list[str] = []
    for resource in catalog:
        for item in resource.get("permissions", []):
            action = item.get("action") or item.get("code", "").split(":", 1)[-1]
            if action in {"view", "detail", "messages", "event_detail", "reveal"}:
                codes.append(item["code"])
    return codes


def seed_categories(token: str) -> None:
    status, existing = _request("GET", "/api/admin/skills/categories", token)
    known = {item.get("name") for item in (existing.get("items", []) if isinstance(existing, dict) else [])}
    for name, keywords in CATEGORIES:
        if name in known:
            continue
        status, body = _request("POST", "/api/admin/skills/categories", token, {"name": name, "keywords": keywords})
        print(f"  分类 {name}: {'已存在' if status == 409 else body if status >= 400 else '已创建'}")


def _skill_files(skill: dict) -> list[tuple[str, bytes]]:
    root = skill["slug"]
    files: list[tuple[str, bytes]] = [(f"{root}/SKILL.md", _skill_markdown(skill))]
    for path, content in skill["files"].items():
        files.append((f"{root}/{path}", content.encode()))
    return files


def seed_skills(token: str) -> dict[str, int]:
    status, listing = _request("GET", "/api/admin/skills", token)
    existing = {}
    if status == 200 and isinstance(listing, dict):
        existing = {item["slug"]: item["id"] for item in listing.get("items", [])}
    result = dict(existing)
    for skill in SKILLS:
        files = _skill_files(skill)
        if skill["slug"] in result:
            status, body = _replace_skill(token, result[skill["slug"]], files, skill["category"])
            print(f"  技能 {skill['slug']}: {'已补齐资源' if status < 400 else f'覆盖失败 {status} {body}'}")
            continue
        status, body = _upload(token, files, skill["category"])
        if status < 400 and isinstance(body, dict) and body.get("items"):
            created = body["items"][0]
            result[created["slug"]] = created["id"]
            print(f"  技能 {skill['slug']}: 已创建 (id={created['id']})")
        else:
            print(f"  技能 {skill['slug']}: 失败 {status} {body}")
    return result


def _replace_skill(token: str, skill_id: int, files: list[tuple[str, bytes]], category: str) -> tuple[int, object]:
    return _multipart(token, f"/api/admin/skills/{skill_id}/replace", files, category)


def seed_bundles(token: str, skill_ids: dict[str, int]) -> None:
    status, listing = _request("GET", "/api/admin/skill-bundles", token)
    existing = {item["name"] for item in (listing if isinstance(listing, list) else [])}
    items = listing.get("items", []) if isinstance(listing, dict) else (listing if isinstance(listing, list) else [])
    by_name = {item["name"]: item for item in items}
    for bundle in BUNDLES:
        members = [skill_ids[slug] for slug in bundle["slugs"] if slug in skill_ids]
        if bundle["name"] in by_name:
            bundle_id = by_name[bundle["name"]]["id"]
            if members:
                _request("POST", f"/api/admin/skill-bundles/{bundle_id}/members", token, {"skill_ids": members})
            print(f"  组合包 {bundle['name']}: 已存在，补齐 {len(members)} 个成员")
            continue
        status, created = _request(
            "POST", "/api/admin/skill-bundles", token, {"name": bundle["name"], "description": bundle["description"]}
        )
        if status >= 400 or not isinstance(created, dict):
            print(f"  组合包 {bundle['name']}: 失败 {status} {created}")
            continue
        if members:
            _request("POST", f"/api/admin/skill-bundles/{created['id']}/members", token, {"skill_ids": members})
        print(f"  组合包 {bundle['name']}: 已创建 ({len(members)} 个成员)")


def seed_roles(token: str) -> None:
    status, roles = _request("GET", "/api/admin/roles", token)
    known = {item["name"]: item for item in (roles if isinstance(roles, list) else [])}
    status, users = _request("GET", "/api/admin/users", token)
    known_users = {item["username"] for item in (users if isinstance(users, list) else [])}
    all_views = view_permissions(token)

    for role in ROLES:
        permissions = role["permissions"] if role["permissions"] is not None else all_views
        if role["name"] in known:
            role_id = known[role["name"]]["id"]
        else:
            status, created = _request(
                "POST", "/api/admin/roles", token, {"name": role["name"], "permissions": permissions}
            )
            if status != 201 or not isinstance(created, dict):
                print(f"  角色 {role['name']}: 失败 {status} {created}")
                continue
            role_id = created["id"]
            print(f"  角色 {role['name']}: 已创建 ({len(permissions)} 项权限)")
        for username in role["users"]:
            if username in known_users:
                continue
            status, created = _request(
                "POST",
                "/api/admin/users",
                token,
                {"username": username, "password": DEMO_PASSWORD, "role_id": role_id},
            )
            print(f"  账号 {username}: {'已创建' if status == 201 else f'失败 {status} {created}'}")


def seed_mcp_keys(token: str) -> None:
    status, catalog = _request("GET", "/api/admin/mcp/catalog", token)
    if status != 200 or not isinstance(catalog, dict):
        return
    capabilities = [item["capability_id"] for item in catalog.get("items", []) if item.get("status") == "enabled"]
    if not capabilities:
        print("  MCP Key: 目录里没有启用能力，跳过")
        return
    status, keys = _request("GET", "/api/admin/mcp/keys", token)
    existing = {item["name"] for item in (keys if isinstance(keys, list) else [])}
    for name in ["验收测试 Key", "验收只读 Key"]:
        if name in existing:
            continue
        status, created = _request(
            "POST",
            "/api/admin/mcp/keys",
            token,
            {"name": name, "capability_ids": capabilities, "rpm_limit": 120, "concurrency_limit": 20},
        )
        print(f"  MCP Key {name}: {'已创建' if status == 201 else f'失败 {status} {created}'}")


def main() -> int:
    token = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    if not token:
        print(f"无法登录 {BASE}（{ADMIN_USERNAME}），请确认后端已启动且密码正确", file=sys.stderr)
        return 1
    print(f"已连接 {BASE}")
    print("1) 分类")
    seed_categories(token)
    print("2) 技能")
    skill_ids = seed_skills(token)
    print("3) 组合包")
    seed_bundles(token, skill_ids)
    print("4) 角色与账号")
    seed_roles(token)
    print("5) MCP Key")
    seed_mcp_keys(token)
    print("\n完成。可登录的账号：")
    print(f"  admin / {ADMIN_PASSWORD}（管理员）")
    for role in ROLES:
        for username in role["users"]:
            print(f"  {username} / {DEMO_PASSWORD}（{role['name']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
