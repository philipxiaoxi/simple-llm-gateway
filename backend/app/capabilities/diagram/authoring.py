"""Archify source 的可调用契约。

模型不读 vendor schema。diagram_create 失败后只能按单条报错补字段，
所以这里把五类图的最小合法 source、必填字段和现场反复猜错的约束一次给全。
"""

from __future__ import annotations

from typing import Any

from .renderer import DIAGRAM_TYPES

_COMPONENT_TYPES = [
    "frontend",
    "backend",
    "database",
    "cloud",
    "security",
    "messagebus",
    "external",
]

# 步骤来自 Archify SKILL.md 的 Native diagram authoring，以及
# docs/authoring-cookbook.zh-CN.md。本网关只暴露下面五类，并改成 diagram_schema / diagram_create。
_GUIDE = {
    "source": "https://github.com/tt-a1i/archify",
    "references": [
        "https://github.com/tt-a1i/archify/blob/main/archify/SKILL.md",
        "https://github.com/tt-a1i/archify/blob/main/docs/authoring-cookbook.zh-CN.md",
    ],
    "choose_type": {
        "architecture": "组件、服务、存储、信任边界。问的是系统由什么组成。",
        "workflow": "有顺序的工作、审批、工具调用、Runbook。问的是先做什么、失败走哪条分支。",
        "sequence": "调用、返回、缓存未命中。问的是谁按什么时序调用谁。",
        "dataflow": "数据移动、转换、下游消费者。问的是数据去了哪里。",
        "lifecycle": "状态、重试、等待、终态。问的是对象现在处于哪一步。",
    },
    "steps": [
        "先根据用户要回答的问题从上表选一个 type。拿不准时选 workflow 画有顺序的步骤，选 sequence 画调用往返。本网关不支持 erd、class、tree、timeline、waterfall、explainer。",
        "用同一个 type 调 diagram_schema，只读返回的 formats.<type>.example。这是形状，不是事实。",
        "复制 example 成完整 source。替换 title、id、label 和连线，保留 schema_version、diagram_type、meta.output、必填数组和节点 type 枚举。",
        "第一张图控制在一条主路径、大约 8 到 12 个节点，只保留解释问题所必需的分支。细节放进 label 或 sublabel。meta 只保留 title、output、locale，不要写 description。",
        "source 内的 diagram_type 必须和工具参数 type 一致。meta.output 固定写 diagram.html。中文图加 meta.locale=zh-CN，它只影响阅读器界面，不翻译你写的文字。",
        "workflow、lifecycle 第一次调用 diagram_create 时传 quality=standard。architecture、sequence、dataflow 才默认 showcase。不要先发探针图。",
        "返回 diagram_invalid 时，只改 diagnostics 点名的字段，优先按 supportedFixes 修，然后用同一 slug 再次 diagram_create。同一处修两轮仍失败，就换一种 schema 允许的结构，不要继续猜字段名。",
    ],
    "workflow_repair": [
        "schema_version 写整数 2，不要写 \"1.0\"。",
        "节点 type 用 frontend、backend、database、cloud、security、messagebus、external。页面动作写 frontend，服务处理写 backend，不要写 process。",
        "同一 lane 的每个 col 只能放一个节点。失败分支放在来源节点相邻的 lane 和相邻 col，不要拉到远端泳道，否则连线会斜穿主路径。",
        "不要用 yOffset 堆叠中文节点。多出来的步骤拆到新 lane。节点总数接近 8 个时，quality 用 standard。",
        "写节点前先算宽度：中文、全角符号算 2，英文和数字算 1，估算像素 = 最长一行的单位数 * 6.8。width 取估算值 + 24，且不超过 280。超长说明放 sublabel，不要堆进 label。",
    ],
}


_RULES = [
    "先调 diagram_schema，再调 diagram_create。不要用通用流程图字段猜 schema。",
    "source 是 Archify 源对象，不是 {type, nodes, edges} 包装。type 只放在工具参数里，source 内写 diagram_type。",
    "schema_version 是整数。workflow/lifecycle 只能是 1 或 2，其余类型只能是 1。不要写 \"1.0\"。",
    "meta.output 必填，且必须是相对路径、以 .html 结尾，例如 diagram.html。不要写绝对路径、URL 或省略扩展名。",
    "id 必须匹配 ^[a-zA-Z][a-zA-Z0-9_-]*$，以字母开头。",
    "additionalProperties 为 false：不要发明 schema 里没有的字段。",
    "节点 type 不是 process/step/action。workflow、architecture、sequence、dataflow 用组件枚举；lifecycle 用状态枚举。",
    "workflow/lifecycle 的 col 是 0 起始的列号，不是步骤序号。同一 lane 内 col 不能重复；分支放到另一条 lane，或用 yOffset 垂直错开。",
    "中文和全角字符按 2 个单位计，英文按 1。节点 width 至少是最长一行单位数 * 6.8 + 24，且不超过 280；更长的内容拆到 sublabel。",
    "校验失败改 source 后重新 diagram_create。平台不会自动补 schema_version、折列或加宽节点。",
]


def _meta(filename: str) -> dict[str, str]:
    return {"title": "Example", "output": filename}


def _workflow_example() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "diagram_type": "workflow",
        "meta": _meta("workflow.html"),
        "lanes": [
            {"id": "main", "label": "主流程"},
            {"id": "fail", "label": "失败"},
        ],
        "nodes": [
            {
                "id": "start",
                "lane": "main",
                "col": 0,
                "type": "frontend",
                "label": "提交",
                "width": 160,
            },
            {
                "id": "check",
                "lane": "main",
                "col": 1,
                "type": "security",
                "label": "校验",
                "width": 160,
            },
            {
                "id": "done",
                "lane": "main",
                "col": 2,
                "type": "backend",
                "label": "完成",
                "width": 160,
            },
            {
                "id": "reject",
                "lane": "fail",
                "col": 1,
                "type": "security",
                "label": "拒绝",
                "width": 160,
            },
        ],
        "edges": [
            {"from": "start", "to": "check"},
            {"from": "check", "to": "done", "label": "通过"},
            {"from": "check", "to": "reject", "label": "失败"},
        ],
    }


def _architecture_example() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": _meta("architecture.html"),
        "components": [
            {"id": "web", "type": "frontend", "label": "Web", "pos": [40, 80], "size": [120, 48]},
            {"id": "api", "type": "backend", "label": "API", "pos": [220, 80], "size": [120, 48]},
        ],
        "connections": [{"from": "web", "to": "api", "label": "调用"}],
    }


def _sequence_example() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "diagram_type": "sequence",
        "meta": _meta("sequence.html"),
        "participants": [
            {"id": "user", "type": "external", "label": "用户"},
            {"id": "api", "type": "backend", "label": "API"},
        ],
        "messages": [{"from": "user", "to": "api", "y": 180, "label": "请求"}],
    }


def _dataflow_example() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "diagram_type": "dataflow",
        "meta": _meta("dataflow.html"),
        "stages": [{"label": "输入"}, {"label": "处理"}],
        "nodes": [
            {"id": "in", "type": "external", "label": "来源", "stage": 0, "row": 0},
            {"id": "job", "type": "backend", "label": "处理", "stage": 1, "row": 0, "width": 160},
        ],
        "flows": [{"from": "in", "to": "job", "label": "数据"}],
    }


def _lifecycle_example() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "diagram_type": "lifecycle",
        "meta": _meta("lifecycle.html"),
        "lanes": [{"id": "main", "label": "状态"}],
        "states": [
            {"id": "draft", "type": "start", "label": "草稿", "lane": "main", "col": 0},
            {"id": "live", "type": "success", "label": "生效", "lane": "main", "col": 1},
        ],
        "transitions": [{"from": "draft", "to": "live"}],
    }


_TYPES: dict[str, dict[str, Any]] = {
    "workflow": {
        "schema_version": [1, 2],
        "required": ["schema_version", "diagram_type", "meta", "lanes", "nodes", "edges"],
        "node_types": _COMPONENT_TYPES,
        "layout": {
            "col": "整数 0..5。同一 lane 内每个 col 只能有一个节点。",
            "lane": "节点 lane 必须引用 lanes[].id。失败分支放在来源节点相邻 lane，且 col 与来源节点相同或相邻。",
            "quality": "8 个以上节点或带中文两行 label 时传 quality=standard，不要用 showcase。",
            "width": "默认 92。中文 label 必须显式加大 width。",
            "meta": "只写 title、output、locale。不要写 description。",
        },
        "node_required": ["id", "lane", "col", "type", "label"],
        "edge_required": ["from", "to"],
        "example": _workflow_example(),
    },
    "architecture": {
        "schema_version": 1,
        "required": ["schema_version", "diagram_type", "meta", "components"],
        "node_types": _COMPONENT_TYPES,
        "layout": {
            "position": "每个 component 都写 pos:[x,y] 和 size:[width,height]。不要只写 row/col，渲染器不会据此自动布局。",
        },
        "node_required": ["id", "type", "label"],
        "edge_required": ["from", "to"],
        "edge_field": "connections",
        "example": _architecture_example(),
    },
    "sequence": {
        "schema_version": 1,
        "required": ["schema_version", "diagram_type", "meta", "participants", "messages"],
        "node_types": _COMPONENT_TYPES,
        "layout": {"y": "messages[].y 是数字且 >= 160，按时间从上到下递增。"},
        "node_required": ["id", "type", "label"],
        "edge_required": ["from", "to", "y", "label"],
        "edge_field": "messages",
        "example": _sequence_example(),
    },
    "dataflow": {
        "schema_version": 1,
        "required": ["schema_version", "diagram_type", "meta", "stages", "nodes", "flows"],
        "node_types": _COMPONENT_TYPES,
        "layout": {
            "stages": "2 到 5 个，只有 label。",
            "stage": "节点 stage/row 为整数 >= 0，stage 不能超过 stages 下标。",
            "width": "可选，最小 48。中文 label 同样要预留宽度。",
        },
        "node_required": ["id", "type", "label", "stage", "row"],
        "edge_required": ["from", "to", "label"],
        "edge_field": "flows",
        "example": _dataflow_example(),
    },
    "lifecycle": {
        "schema_version": [1, 2],
        "required": ["schema_version", "diagram_type", "meta", "lanes", "states", "transitions"],
        "node_types": [
            "start",
            "active",
            "waiting",
            "decision",
            "success",
            "failure",
            "neutral",
            "external",
        ],
        "layout": {
            "col": "整数 0..4。",
            "lanes": "1 到 4 条。",
        },
        "node_required": ["id", "type", "label", "lane", "col"],
        "node_field": "states",
        "edge_required": ["from", "to"],
        "edge_field": "transitions",
        "example": _lifecycle_example(),
    },
}


def diagram_schema(diagram_type: str | None = None) -> dict[str, Any]:
    """返回模型可直接照抄的格式说明。type 为空时给五类摘要，指定时只给该类。"""
    selected = str(diagram_type or "").strip().lower()
    if selected and selected not in DIAGRAM_TYPES:
        raise ValueError(selected)
    types = {selected: _TYPES[selected]} if selected else _TYPES
    return {
        "guide": _GUIDE,
        "types": list(DIAGRAM_TYPES),
        "rules": _RULES,
        "id_pattern": "^[a-zA-Z][a-zA-Z0-9_-]*$",
        "meta_output": "相对路径且以 .html 结尾，例如 diagram.html",
        "formats": types,
    }
