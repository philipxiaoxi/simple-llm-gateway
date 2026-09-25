# Requirements Document

## Introduction

在 MCP 广场新增能力 `docparse`：把 Word、PDF、Excel、PPT 等办公文档转成 Markdown。管理端可上传并预览结果；持有 `docparse` 授权的 MCP Key 可通过 REST `/v1/capabilities/docparse/*` 与 MCP tools 调用同一套转换服务。转换结果可下载，也可一键送入已有知识库采集队列。

本能力做格式转换与结构保留，不负责 OCR 纠错模型训练。知识库上传这些格式时必须先经同一转换器得到 Markdown，再进入既有分块、向量化与检索。`docparse` 必须作为对外 MCP 服务的一组工具挂在现有 `/mcp` 上。

## Glossary

- **DocParse**：MCP 广场能力，标识 `docparse`，负责办公文档到 Markdown 的转换。
- **MCP 广场**：管理端 `/mcp-plaza` 及其能力注册表、MCP Key 白名单、调用日志。
- **MCP Key**：前缀 `mcp-` 的独立密钥，与聊天 `sk-` 分表。
- **源文件**：用户提交的 `.pdf`、`.docx`、`.doc`、`.xlsx`、`.xls`、`.pptx`、`.ppt`、`.html`、`.htm` 文件。
- **转换任务**：一次源文件到 Markdown 的异步作业，状态为 `queued`、`running`、`succeeded`、`failed`、`cancelled`。
- **Markdown 结果**：转换成功后的 UTF-8 Markdown 文本，以及可选的图片附件清单。
- **结构保留**：标题层级、段落、列表、表格、超链接在 Markdown 中可对应还原。
- **扫描页**：PDF 页面中没有可提取文本层、需要 OCR 才能得到文字的页面。
- **知识库送入**：把 Markdown 结果写入已有知识库采集队列，后续由知识库能力分块并检索。
- **知识库直传**：在知识库上传或目录批量入库时，受支持的办公文档不跳过，而是先转换成 Markdown 再创建采集任务。
- **对外 MCP 服务**：现有 `https://站点/mcp` Streamable HTTP 端点。已授权 Key 调用 `tools/list` 与 `tools/call` 即可使用 `docparse_*`，不另开端口。

## Requirements

### Requirement 1: 能力注册与授权

**User Story:** AS 平台管理员, I WANT `docparse` 出现在 MCP 广场并按 Key 授权, SO THAT 下游只能调用被允许的转换能力。

#### Acceptance Criteria

1. THE MCP 广场 SHALL 在服务目录展示已启用的 `docparse` 能力，包含名称、说明、版本和管理入口。
2. WHEN 管理员为 MCP Key 勾选 `docparse`, THE 系统 SHALL 允许该 Key 调用 DocParse 的 REST 与 MCP 接口。
3. IF MCP Key 未授权 `docparse`, THE 系统 SHALL 拒绝该 Key 的 DocParse 调用并返回 403。
4. IF 请求携带聊天 `sk-` 密钥访问 DocParse 的 MCP 或 REST 路径, THE 系统 SHALL 返回 401。
5. THE 系统 SHALL 把每次 DocParse 调用写入既有 MCP 调用日志，记录能力标识、操作名、成功或失败、耗时和错误信息。

### Requirement 2: 管理端转换

**User Story:** AS 管理员, I WANT 在 MCP 广场上传办公文档并看到 Markdown, SO THAT 我可以先验证转换质量再交给下游使用。

#### Acceptance Criteria

1. WHEN 管理员在 DocParse 页面提交一个受支持的源文件, THE 系统 SHALL 创建转换任务并返回任务标识。
2. WHILE 转换任务处于 `queued` 或 `running`, THE 系统 SHALL 提供可查询的进度，至少包含状态、阶段和百分比。
3. WHEN 转换任务变为 `succeeded`, THE 系统 SHALL 展示 Markdown 预览，并提供 Markdown 文件下载。
4. WHEN 管理员取消尚未完成的转换任务, THE 系统 SHALL 将任务标记为 `cancelled` 并停止后续写入。
5. THE DocParse 管理页 SHALL 在 PC 与移动端均可完成上传、查看进度、预览和下载；PC 保持信息密度，移动端避免控件挤压换行。

### Requirement 3: 协议面转换

**User Story:** AS 集成方, I WANT 用 MCP 或 REST 提交文档并取回 Markdown, SO THAT Agent 与业务系统可以自动消费转换结果。

#### Acceptance Criteria

1. WHEN 已授权 Key 向 `POST /v1/capabilities/docparse/jobs` 提交受支持的源文件, THE 系统 SHALL 创建转换任务并返回任务标识与初始状态。
2. WHEN 已授权 Key 查询自己的转换任务, THE 系统 SHALL 返回该任务的状态、错误信息和结果是否可下载。
3. WHEN 转换任务状态为 `succeeded`, THE 系统 SHALL 通过结果接口返回 Markdown 文本，或通过下载接口返回 `text/markdown` 文件。
4. THE 系统 SHALL 提供 MCP tools `docparse_convert`、`docparse_job`、`docparse_result`，其业务结果与对应 REST 操作一致。
5. IF 已授权 Key 查询不属于该 Key 的转换任务, THE 系统 SHALL 返回 404。

### Requirement 4: 格式与结构

**User Story:** AS 使用者, I WANT Word、PDF、表格和幻灯片变成可读 Markdown, SO THAT 后续检索和人工阅读都能用同一份文本。

#### Acceptance Criteria

1. THE 系统 SHALL 接受扩展名为 `.pdf`、`.docx`、`.xlsx`、`.pptx`、`.html`、`.htm` 的源文件。
2. WHEN 源文件为 `.docx` 且包含标题、段落、有序或无序列表、表格和超链接, THE 系统 SHALL 在 Markdown 中分别输出对应的标题、段落、列表、GFM 表格和链接。
3. WHEN 源文件为带文本层的 PDF, THE 系统 SHALL 按阅读顺序提取文本，并以分页标记区分页面。
4. WHEN 源文件为 `.xlsx`, THE 系统 SHALL 为每个工作表输出二级标题，并把单元格区域输出为 GFM 表格。
5. WHEN 源文件为 `.pptx`, THE 系统 SHALL 为每张幻灯片输出二级标题，并按形状阅读顺序输出文本框内容。
6. WHERE 部署环境安装了对应转换器, THE 系统 SHALL 接受 `.doc`、`.xls`、`.ppt` 并先转为 Office Open XML 再提取。
7. IF 源文件扩展名不在受支持列表, THE 系统 SHALL 拒绝创建任务并返回 400，说明允许的扩展名。

### Requirement 5: 图片、扫描件与限制

**User Story:** AS 使用者, I WANT 知道扫描页和超限文件会怎样处理, SO THAT 失败可预期、可重试。

#### Acceptance Criteria

1. IF PDF 页面没有可提取文本层, THE 系统 SHALL 将该页标记为扫描页；当 OCR 已启用时提取该页文字，当 OCR 未启用时在 Markdown 中写入扫描页占位说明。
2. WHEN 源文件内嵌图片且图片提取已启用, THE 系统 SHALL 在 Markdown 中保留图片引用，并允许随结果下载这些图片。
3. IF 单个源文件超过 30MB, THE 系统 SHALL 拒绝该文件并返回 400。
4. IF PDF 页数超过 200, THE 系统 SHALL 拒绝该文件并返回 400。
5. IF 转换过程失败, THE 系统 SHALL 将任务标记为 `failed`，保存可展示的错误信息，并允许管理员或任务所属 Key 重试。
6. THE 系统 SHALL 在转换完成后的 7 天内保留源文件与 Markdown 结果，到期后删除文件并保留任务元数据。

### Requirement 6: 知识库直接支持办公文档

**User Story:** AS 管理员, I WANT 在知识库里直接上传 Word、PDF、Excel、PPT, SO THAT 这些文件进入检索，而不是被当成非文本跳过。

#### Acceptance Criteria

1. WHEN 管理员向知识库提交受支持的办公文档, THE 系统 SHALL 先将其转换为 Markdown，再创建知识库采集任务。
2. WHEN 目录批量入库遇到受支持的办公文档, THE 系统 SHALL 转换后入库，并在跳过清单之外计入成功创建数。
3. THE 采集任务的来源名 SHALL 保留原文件名并将扩展名改为 `.md`，采集原文 SHALL 为转换得到的 Markdown。
4. IF 转换失败, THE 系统 SHALL 不创建采集任务；单文件上传返回 400，批量入库将该文件写入跳过清单并继续处理其余文件。
5. WHEN 管理员对状态为 `succeeded` 的 DocParse 任务选择目标知识库并确认送入, THE 系统 SHALL 以已有 Markdown 创建一个知识库采集任务，并记录采集任务标识。
6. IF 目标知识库不存在, THE 系统 SHALL 拒绝送入并返回 404。

### Requirement 7: 对外 MCP 服务

**User Story:** AS 集成方, I WANT 用现有 MCP 端点调用文档转换, SO THAT Agent 不需要单独接一套转换服务。

#### Acceptance Criteria

1. THE 系统 SHALL 在现有 MCP 端点 `/mcp` 上暴露 `docparse_convert`、`docparse_job`、`docparse_result`。
2. WHEN 已授权 `docparse` 的 MCP Key 调用 `tools/list`, THE 系统 SHALL 在工具列表中包含上述三个工具及其输入参数说明。
3. WHEN 未授权 `docparse` 的 MCP Key 调用 `tools/list`, THE 系统 SHALL 不返回 `docparse_*` 工具。
4. WHEN 已授权 Key 调用 `docparse_convert` 并提交受支持的源文件, THE 系统 SHALL 创建转换任务并返回任务标识与初始状态。
5. WHEN 已授权 Key 对状态为 `succeeded` 的自有任务调用 `docparse_result`, THE 系统 SHALL 返回 Markdown 文本。
6. IF 未授权 Key 调用任一 `docparse_*` 工具, THE 系统 SHALL 拒绝该调用并返回权限错误。
7. THE MCP 工具的转换结果 SHALL 与同一任务的 REST 结果一致。
