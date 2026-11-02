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
        "先按官方布局分类每条边，再填字段。主路径上的节点按阅读顺序放在相邻 col。分支或存储放在所属节点正上或正下，使连线是一条直线。回边必须走没有分支的一侧，不能和主路径挤在同一走廊。",
        "source 内的 diagram_type 必须和工具参数 type 一致。meta 只写 title、output、locale，不要写 description、subtitle、visual_preset。中文图加 meta.locale=zh-CN。",
        "不要写 via、channelX、channelY、labelAt，先让编译器自动布线。中文 workflow 或节点超过 8 个时传 quality=standard；英文短图才用 showcase。不要先发探针图。",
        "返回 diagram_invalid 时，只改 diagnostics 点名的字段，优先按 supportedFixes 修，然后用同一 slug 再次 diagram_create。同一处修两轮仍失败，就换一种 schema 允许的结构，不要继续猜字段名。",
    ],
    "workflow": [
        "新 workflow 用 schema_version 整数 2。lane 表示责任边界，col 0..5 表示从左到右的进展，不是步骤序号。",
        "主路径保持单调。失败、拒绝、重试放进 lane.variant=exception 的独立泳道，路由要离开主泳道走廊。",
        "回边放到主路径没有分支的一侧。若回边会穿过其他边，移动不在主路径上的那个端点，不要加 via。",
        "同一 lane 且垂直位置重叠的节点至少相距 8px。不要把两个节点放在同一 lane 的同一 col，除非它们用不同 yOffset 做垂直堆叠。",
        "节点 type 只能是 frontend、backend、database、cloud、security、messagebus、external。中文算 2 个单位，width 至少为最长一行单位数 * 6.8 + 24。中文多节点图用 quality=standard，showcase 的桌面可读性会拒绝过高的泳道图。",
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
    "workflow 的 col 是 0..5 的进展列。失败泳道用 variant=exception，回边走空走廊，不和主路径共用走廊。",
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
            {"id": "fail", "label": "失败", "variant": "exception"},
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
            "col": "整数 0..5，表示从左到右的进展。主路径按阅读顺序放相邻列。",
            "lane": "lane 表示责任边界。失败、拒绝、重试使用 variant=exception，并把回边放到没有分支的一侧。",
            "route": "不要写 via、channelX、channelY、labelAt。先用自动布线，只有 diagnostics 点名后才加一个几何字段。",
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
