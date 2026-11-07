# Requirements Document

## Introduction

接入 `philipxiaoxi/handraw-style` 手绘风格库到 MCP 广场，作为新的参考图能力，用于按图选风格并作为图片生成的参考输入。

## Glossary

- **手绘风格库**：上游仓库 `philipxiaoxi/handraw-style` 提供的编号化视觉风格索引与示例图。
- **MCP 广场**：平台内统一注册、授权和调用 MCP 能力的入口。
- **参考图**：用于图片生成时约束画面风格或构图的输入图片。

## Requirements

### Requirement 1: 手绘风格检索

**User Story:** AS 创作用户, I want 在 MCP 广场按编号 / 家族 / 关键词检索手绘风格, so that 我能快速找到目标画风。

#### Acceptance Criteria

1. WHEN 用户打开 MCP 广场中的手绘风格库, THE 系统 SHALL 展示按家族、参考作者、编号和关键词过滤的风格列表。
2. WHEN 用户输入关键词, THE 系统 SHALL 返回匹配的风格图例、示例图直链与核心特征提示词。
3. IF 上游风格索引不可用, THE 系统 SHALL 返回明确的失败信息。

### Requirement 2: 风格参考图用于生成

**User Story:** AS 创作用户, I want 在图片生成页直接选取手绘风格图作为参考图, so that AI 能照着所选风格出图。

#### Acceptance Criteria

1. WHEN 用户在图片生成页打开参考图选择器, THE 系统 SHALL 提供手绘风格库入口。
2. WHEN 用户选中某条手绘风格, THE 系统 SHALL 将该风格示例图作为参考图传入图片生成流程。
3. IF 用户只查看风格而不用于生成, THE 系统 SHALL 保持浏览与提示词复制能力可用。

### Requirement 3: 权限与接入

**User Story:** AS 平台管理员, I want 为手绘风格库单独授权, so that 不同角色可以按需访问该能力。

#### Acceptance Criteria

1. WHEN 管理员配置角色权限, THE 系统 SHALL 提供 `mcp_handdraws:view`、`sync`、`create`、`delete` 权限点。
2. WHEN MCP Key 绑定能力, THE 系统 SHALL 支持将 `handdraw` 能力授权给该 Key。
3. IF 用户缺少对应权限, THE 系统 SHALL 拒绝访问手绘风格库接口。

