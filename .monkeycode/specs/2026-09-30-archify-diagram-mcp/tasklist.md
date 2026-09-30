# Tasklist: Archify 图表 MCP 能力（Diagram）

Feature Name: 2026-09-30-archify-diagram-mcp

## 0. 前置

- [x] 确认命名：能力 `diagram` / 展示名「图表生成」
- [x] 确认数据模型：复用 `sites` + `origin` 判别列
- [x] 确认管理端：新建独立图表页

## 1. 后端：渲染器与能力骨架

- [x] vendor Archify 3.0.1 子集到 `backend/app/capabilities/diagram/vendor/archify/`
- [x] `capabilities/diagram/errors.py`：`DiagramError`
- [x] `capabilities/diagram/renderer.py`：沙箱调用 CLI，解析 `deliver --json`
- [x] `capabilities/diagram/service.py`：渲染 → 单文件 zip → 站点流水线
- [x] `capabilities/diagram/provider.py`：`CapabilitySpec` + `dispatch` + MCP tools
- [x] `capabilities/diagram/__init__.py`
- [x] `capabilities/registry.py` 注册 `DiagramProvider`
- [x] `capabilities/runtime.py` 归一化 `DiagramError`

## 2. 后端：数据模型与配置

- [x] `models.py`：`Site.origin`、`SiteVersion.diagram_type/source_json/quality`
- [x] `db.py`：幂等 `ALTER TABLE` 迁移
- [x] `config.py`：`diagram_*` 设置项与 vendor 路径解析
- [x] `sites.py`：`create_deploy` 支持 origin/源字段；`list_sites` 支持 origin 过滤

## 3. 后端：协议与管理面

- [x] `routers/diagrams_public.py`：`/v1/diagrams`
- [x] `routers/admin_mcp_diagrams.py`：`/api/admin/mcp/diagrams`
- [x] `main.py`：注册两个 router

## 4. 部署

- [x] `Dockerfile`：运行镜像安装 Node.js
- [x] `config.py` 的 env 兜底后 `.env.example` 增加配置说明

## 5. 前端

- [x] `lib/api.ts`：图表类型与 API 封装
- [x] `pages/McpDiagrams.tsx`：列表 + 粘贴 JSON 源创建
- [x] `pages/McpDiagramDetail.tsx`：预览/版本/源/令牌/编辑/删除
- [x] `App.tsx`：注册路由
- [x] `pages/McpPlaza.tsx`：`iconMap` 增加 `shapes`
- [x] `pages/McpDocs.tsx`：接入说明由能力目录自动带出 `diagram` 工具与 REST 端点

## 6. 验证

- [x] `backend/tests/test_diagrams.py`
- [x] `cd backend && PYTHONPATH=. python3 -m pytest tests/test_diagrams.py tests/test_mcp_integration.py`（20+3 通过）
- [x] `cd frontend && npx tsc -b`
