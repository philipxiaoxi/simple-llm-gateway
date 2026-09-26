# Requirements Document

## Introduction

现有「接入说明」是一个与 Key 无关的静态速查页：无法体现「这把 Key 实际能做什么」，没有一键复制给 AI 的提示词，且只覆盖 MCP 一种范式。本需求把该页升级为按 MCP Key 生成的**接入中心**：选择 Key 后只展示其已授权能力，并按多种接入方式生成可整段复制给 AI 的提示词——包括 MCP 方式与「Skill + REST」的提示词方式，兼顾不支持 MCP 或不愿用 MCP 的客户端。

交互参考现有 Share 页「选择模型并复制 AI 配置」：先选对象，再一键复制一段自然语言说明；复制失败时提供可全选的文本框兜底。文案风格参考 `skillInstall.ts` 的安装提示词。

已确认范围：提示词直接包含明文密钥；Skill + REST 只提供「复制提示词」，不产出可下载的 SKILL.md；MCP 客户端配置只给通用 JSON，由 AI 自行适配客户端。

## Glossary

- **接入中心**：管理端「接入说明」页升级后的页面，路由保持 `/mcp-plaza/docs`。
- **接入方式**：MCP、Skill + REST、客户端配置、快速参考四种范式。
- **能力清单**：某把 MCP Key 通过白名单授权的能力集合。
- **可用能力**：能力清单内的能力。**未授权能力**：目录中存在但该 Key 未勾选的能力。
- **提示词包**：一段可整段复制给任意 AI 客户端的文本，说明如何接入与使用。
- **Skill + REST**：不通过 MCP，由提示词指导 AI 直接发起 HTTP 调用的接入方式。
- **MCP Key**：前缀 `mcp-`、与聊天 `sk-` 分表的独立密钥。

## Requirements

### Requirement 1: 按 Key 生成接入信息

**User Story:** AS 管理员, I WANT 选择一把 MCP Key 后看到它真正能做什么, SO THAT 我不会把无效配置发给下游。

#### Acceptance Criteria

1. WHILE 管理员打开接入中心, THE 系统 SHALL 提供 MCP Key 选择器并默认选中最近使用的 Key。
2. WHEN 接入中心通过 `?key=<id>` 打开, THE 系统 SHALL 预选该 Key。
3. WHEN 管理员选择一个 Key, THE 系统 SHALL 展示该 Key 的名称、前缀、状态、MCP 端点、REST Base URL 与鉴权方式。
4. THE 系统 SHALL 把该 Key 已授权能力标记为可用，把未授权能力标记为未授权。
5. IF 系统内不存在 MCP Key, THE 接入中心 SHALL 提示前往创建并隐藏复制操作。

### Requirement 2: 多种接入方式

**User Story:** AS 管理员, I WANT 按客户端能力选择接入方式, SO THAT 支持 MCP 的客户端和只支持 HTTP 的客户端都能接上。

#### Acceptance Criteria

1. THE 接入中心 SHALL 至少提供 MCP、Skill + REST、客户端配置、快速参考四种接入方式。
2. WHILE 显示 MCP 方式, THE 系统 SHALL 展示 MCP URL、鉴权 Header 与该 Key 可用工具的清单。
3. WHILE 显示 Skill + REST 方式, THE 系统 SHALL 展示 REST Base URL、鉴权方式与按能力分组的 REST 端点。
4. WHILE 显示客户端配置方式, THE 系统 SHALL 提供一段通用 MCP 客户端 JSON 配置。
5. WHILE 显示快速参考, THE 系统 SHALL 按能力展示可直接复制的 REST 与 MCP 调用示例。

### Requirement 3: 复制为 AI 提示词

**User Story:** AS 管理员, I WANT 一键复制可整段发给 AI 的说明, SO THAT AI 能自己完成接入与调用。

#### Acceptance Criteria

1. WHEN 管理员在 MCP 或 Skill + REST 方式选择「复制为 AI 提示词」, THE 系统 SHALL 打开能力多选并默认全选该 Key 已授权能力。
2. WHEN 管理员确认复制, THE 系统 SHALL 生成仅包含所选且已授权能力的提示词并写入剪贴板。
3. THE MCP 提示词 SHALL 包含接入名称、MCP URL、传输方式、鉴权 Header、可用工具签名与调用约束。
4. THE Skill + REST 提示词 SHALL 包含 Base URL、鉴权方式、可用 REST 端点与调用约束。
5. IF 剪贴板写入失败, THE 系统 SHALL 展示可全选复制的文本框。
6. WHEN 复制成功, THE 系统 SHALL 展示成功提示。

### Requirement 4: 密钥展示

**User Story:** AS 管理员, I WANT 能复制密钥, SO THAT 配置提示词时不用回列表查找。

#### Acceptance Criteria

1. THE 接入中心 SHALL 提供复制完整密钥的操作，并复用管理员查看密钥接口。
2. THE 系统 SHALL 在生成的提示词中直接写入明文密钥。

### Requirement 5: 能力文档元数据

**User Story:** AS 平台维护者, I WANT 接入说明随能力注册自动更新, SO THAT 新增能力不用改接入页。

#### Acceptance Criteria

1. THE 系统 SHALL 为每个能力提供说明、MCP 工具定义、REST 端点示例与注意事项。
2. WHEN 后端注册新能力, THE 接入中心 SHALL 自动展示该能力，无需修改接入中心页面代码。
3. THE 系统 SHALL 在能力目录接口返回接入所需的元数据。

### Requirement 6: 入口与响应式

**User Story:** AS 管理员, I WANT 从 Key 列表直接进入接入配置, SO THAT 配置路径最短。

#### Acceptance Criteria

1. WHEN 管理员在 MCP Key 列表点击「接入」, THE 系统 SHALL 跳转接入中心并预选该 Key。
2. THE 接入中心 SHALL 在 PC 与移动端均可完成选择、复制与查看示例。
3. IF 某接入方式对该 Key 无可用能力, THE 系统 SHALL 展示空状态说明而不是空内容。
