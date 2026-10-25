"""用户与角色权限的单一真源。

权限点形如 `{资源}:{动作}`：资源对应管理后台的一个页面/模块，动作对应页面上的具体功能。
本模块同时供三处使用：

- 后端校验（`services/permission_guard.py`）：路由解析出的权限码必须落在 `CATALOG` 内；
- 角色编辑页（`GET /api/admin/permission-catalog`）：按页面分组渲染勾选项；
- 校验角色提交的权限码是否合法（`validate_codes`）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

ADMIN_ROLE_NAME = "管理员"
WILDCARD = "*"

# 仅用于控制页面入口、没有对应管理接口的权限码（覆盖率测试会放行这些）
PAGE_ONLY_CODES: frozenset[str] = frozenset()

# 资源 -> (页面名, ((动作, 动作名), ...))
CATALOG: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "dashboard": ("概览", (("view", "查看"),)),
    "accounts": (
        "上游账号",
        (
            ("view", "查看"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("import", "导入"),
            ("export", "导出"),
            ("probe", "连通性测试"),
            ("quota", "刷新额度"),
            ("models_manage", "模型目录维护"),
        ),
    ),
    "agents": ("网关代理", (("view", "查看"), ("route_bind", "绑定路由模型"))),
    "keys": (
        "API Key",
        (
            ("view", "查看列表"),
            ("reveal", "查看/复制密钥明文"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("aliases_manage", "别名管理"),
            ("cc_switch", "CC Switch 配置"),
        ),
    ),
    "logs": (
        "记录审计",
        (
            ("view", "查看列表"),
            ("detail", "查看详情"),
            ("messages", "查看对话与思考内容"),
            ("export", "导出"),
        ),
    ),
    # 模型测速页单一权限：进入即具备测速能力；测速结果查看走测速历史页的权限
    "benchmark": ("模型测速", (("view", "进入并测速"),)),
    "benchmark_groups": ("测速分组", (("view", "查看"), ("manage", "维护分组"))),
    "benchmark_history": (
        "测速历史",
        (("view", "查看列表"), ("detail", "查看详情"), ("export", "导出")),
    ),
    "leaderboard": ("模型榜", (("view", "查看"), ("export", "导出"))),
    "info": (
        "资讯收集",
        (
            ("view", "查看"),
            ("preview", "源预览"),
            ("source_create", "新增源"),
            ("source_update", "编辑源"),
            ("source_delete", "删除源"),
            ("collect", "采集"),
            ("article_create", "新增文章"),
            ("article_delete", "删除文章"),
            ("favorite", "收藏"),
            ("hidden", "隐藏"),
            ("featured", "推荐"),
            ("ai_skip", "AI 跳过"),
            ("ai_settings", "AI 评分设置"),
            ("rescore", "重新评分"),
        ),
    ),
    "image_gen": (
        "图片生成",
        (
            ("view", "查看"),
            ("task_create", "创建任务"),
            ("task_retry", "重试任务"),
            ("task_delete", "删除任务"),
        ),
    ),
    "video_gen": (
        "视频生成",
        (
            ("view", "查看"),
            ("task_create", "创建任务"),
            ("task_retry", "重试任务"),
            ("task_delete", "删除任务"),
        ),
    ),
    "generation": ("生成配置（图片/视频共用）", (("settings", "配置供应商与默认模型"), ("upload", "参考素材上传"))),
    "offline": (
        "离线下载",
        (
            ("view", "查看"),
            ("resolve", "解析上游"),
            ("task_create", "创建任务"),
            ("task_delete", "删除任务"),
            ("retry", "重试"),
        ),
    ),
    "skills": (
        "Skills",
        (
            ("view", "查看"),
            ("upload", "上传/替换"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("category_manage", "分类管理"),
            ("analysis", "AI 解析"),
            ("download", "下载"),
        ),
    ),
    "skill_bundles": (
        "Skills 组合包",
        (
            ("view", "查看"),
            ("download", "下载"),
            ("delete", "删除"),
            ("manage", "维护组合包"),
        ),
    ),
    "security": (
        "安全中心",
        (("view", "查看"), ("event_detail", "查看事件详情"), ("settings", "安全设置")),
    ),
    "mcp_calls": ("MCP 调用日志", (("view", "查看"),)),
    "mcp_info": ("MCP 资讯上报", (("view", "查看上报历史"),)),
    "mcp_keys": (
        "MCP Key",
        (
            ("view", "查看"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("capabilities_manage", "能力授权"),
            ("reveal", "查看密钥明文"),
        ),
    ),
    "mcp_knowledge": (
        "MCP 知识库",
        (
            ("view", "查看"),
            ("kb_create", "新增知识库"),
            ("kb_update", "编辑知识库"),
            ("kb_delete", "删除知识库"),
            ("doc_upload", "上传文档"),
            ("doc_delete", "删除文档"),
            ("doc_reembed", "重嵌入"),
            ("search", "检索"),
        ),
    ),
    "mcp_docparse": (
        "MCP 文档解析",
        (
            ("view", "查看"),
            ("job_create", "创建任务"),
            ("job_cancel", "取消任务"),
            ("job_retry", "重试任务"),
            ("job_delete", "删除任务"),
            ("download", "下载产物"),
            ("ingest", "写入知识库"),
        ),
    ),
    "mcp_sites": (
        "MCP 站点",
        (
            ("view", "查看"),
            ("site_create", "新增站点"),
            ("site_update", "编辑站点"),
            ("site_delete", "删除站点"),
            ("version_create", "创建版本"),
            ("version_activate", "启用版本"),
            ("version_retry", "重试版本"),
            ("version_delete", "删除版本"),
            ("token_manage", "访问令牌"),
        ),
    ),
    "mcp_douyin": (
        "MCP 抖音",
        (
            ("view", "查看"),
            ("provider_manage", "供应商配置"),
            ("job_create", "创建任务"),
            ("job_retry", "重试任务"),
            ("job_delete", "删除任务"),
        ),
    ),
    "mcp_browser": (
        "MCP 在线浏览器",
        (
            ("view", "查看"),
            ("screenshot", "主动截图"),
            ("close", "关闭会话"),
        ),
    ),
    "mcp_diagrams": (
        "MCP 图表",
        (
            ("view", "查看"),
            ("generate", "生成"),
            ("update", "编辑"),
            ("rollback", "回滚"),
            ("delete", "删除"),
            ("token_manage", "访问令牌"),
        ),
    ),
    "tools": (
        "工具中心",
        (
            ("view", "查看"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("run", "执行/下载"),
            ("stop", "停止"),
            ("script_edit", "脚本编辑"),
        ),
    ),
    "jobs": ("定时任务", (("view", "查看"), ("run", "立即执行"), ("edit", "编辑调度"))),
    "voice": ("语音房", (("view", "查看"), ("manage", "管理"))),
    "integrations": ("第三方集成", (("view", "查看"), ("update", "配置"))),
    "public_gate": ("公开门禁", (("view", "查看"), ("update", "配置"))),
    "users": (
        "用户管理",
        (
            ("view", "查看"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
            ("reset_password", "重置密码"),
            ("toggle_status", "启用/停用"),
        ),
    ),
    "roles": (
        "角色管理",
        (
            ("view", "查看"),
            ("create", "新增"),
            ("update", "编辑"),
            ("delete", "删除"),
        ),
    ),
}


# 能力 -> 进入该能力所需的查看权限（任一命中即可）。
# MCP 广场按此按功能划分：目录接口按当前账号权限过滤，前端只渲染返回的卡片。
CAPABILITY_VIEW_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "knowledge": ("mcp_knowledge:view",),
    "docparse": ("mcp_docparse:view",),
    "site": ("mcp_sites:view",),
    "douyin": ("mcp_douyin:view",),
    "browser": ("mcp_browser:view",),
    "diagram": ("mcp_diagrams:view",),
    "info": ("mcp_info:view",),
    "generation": ("generation:settings",),
}


def iter_catalog() -> Iterator[tuple[str, str, str, str]]:
    """展开为 (资源, 页面名, 动作, 动作名)。"""
    for resource, (label, actions) in CATALOG.items():
        for action, action_label in actions:
            yield resource, label, action, action_label


def all_codes() -> set[str]:
    return {f"{resource}:{action}" for resource, _, action, _ in iter_catalog()}


def resource_label(resource: str) -> str:
    entry = CATALOG.get(resource)
    return entry[0] if entry else resource


def code_label(code: str) -> str:
    if code == WILDCARD:
        return "全部权限"
    resource, _, action = code.partition(":")
    entry = CATALOG.get(resource)
    if not entry:
        return code
    page = entry[0]
    for candidate, label in entry[1]:
        if candidate == action:
            return f"{page} · {label}"
    return f"{page} · {action}"


def validate_codes(codes: Iterable[str]) -> list[str]:
    """返回不在目录内的权限码（去重、保持稳定顺序）。"""
    known = all_codes()
    unknown: list[str] = []
    for code in codes:
        if not isinstance(code, str) or code in known or code == WILDCARD:
            continue
        if code not in unknown:
            unknown.append(code)
    return unknown


def catalog_payload() -> list[dict[str, object]]:
    """角色编辑页用的目录结构：按页面分组，含动作与中文名。"""
    return [
        {
            "resource": resource,
            "label": label,
            "permissions": [
                {"code": f"{resource}:{action}", "action": action, "label": action_label}
                for action, action_label in actions
            ],
        }
        for resource, (label, actions) in CATALOG.items()
    ]
