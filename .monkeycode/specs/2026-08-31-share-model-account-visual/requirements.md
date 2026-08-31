# API Key 自助查询模型账号归属需求

## Introduction

API Key 可以绑定多个上游账号，当前自助查询页仅展示合并后的模型名称。用户需要直接识别每个模型所属账号，并通过一致的视觉编码快速区分不同账号。

## Glossary

- **自助查询页**：用户输入完整 API Key 后查看账号、模型、用量和导入配置的公开页面。
- **绑定账号**：当前 API Key 按优先级绑定的上游账号或网关账号。
- **公开模型 ID**：客户端调用网关时使用的模型名称，模型冲突时可能带账号前缀。
- **账号色**：根据绑定优先顺序分配给账号的界面强调色。
- **模型选择区域**：可用模型、手动配置、AI 配置、CC Switch 和 VSCode 导入中的模型列表或选择器。

## Requirements

### Requirement 1: 返回模型账号归属

**User Story:** AS 自助查询用户, I want 查看模型所属账号, so that 我可以选择符合预期来源的模型。

#### Acceptance Criteria

1. WHEN 自助查询接口返回可用模型时，系统 SHALL 为每个公开模型 ID 返回所属账号的 ID、名称、来源、供应商和绑定优先级。
2. WHEN 多个账号包含相同原始模型 ID 时，系统 SHALL 保持模型归属与实际路由目录一致。
3. WHEN 账号处于不可用状态时，系统 SHALL 从可用模型归属列表中排除该账号的模型。
4. WHEN 公开模型 ID 带账号前缀时，系统 SHALL 返回对应的原始模型 ID，供界面补充说明。

### Requirement 2: 按账号分组展示模型

**User Story:** AS 自助查询用户, I want 按账号浏览模型, so that 我可以快速理解模型目录结构。

#### Acceptance Criteria

1. WHEN 查询结果包含模型时，自助查询页 SHALL 按绑定账号优先级分组展示可用模型。
2. WHEN 展示账号分组时，自助查询页 SHALL 显示账号名称、账号来源、优先级和模型数量。
3. WHEN 展示模型标签时，自助查询页 SHALL 保留完整公开模型 ID，并允许长名称换行或截断后通过标题查看全文。
4. WHILE 页面宽度小于 640 像素，自助查询页 SHALL 使用单列账号分组和可换行模型标签。
5. WHILE 页面宽度大于或等于 640 像素，自助查询页 SHALL 使用适合内容宽度的双列账号分组。

### Requirement 3: 使用账号色建立视觉关联

**User Story:** AS 自助查询用户, I want 通过颜色识别账号, so that 我可以一眼关联账号与模型。

#### Acceptance Criteria

1. WHEN 展示第一个绑定账号时，自助查询页 SHALL 使用 Signal 绿色作为账号色。
2. WHEN 展示后续绑定账号时，自助查询页 SHALL 按固定调色板和绑定顺序分配账号色。
3. WHEN 同一账号出现在多个模型选择区域时，自助查询页 SHALL 使用相同账号色。
4. WHEN 账号数量超过调色板容量时，自助查询页 SHALL 循环使用调色板，并持续显示账号名称作为文本识别信息。
5. WHILE 使用账号色时，自助查询页 SHALL 同时使用账号名称或账号徽标表达归属，以支持色觉差异用户。

### Requirement 4: 在全部模型选择区域显示归属

**User Story:** AS 自助查询用户, I want 在配置流程中持续看到模型归属, so that 我可以避免选择错误账号的模型。

#### Acceptance Criteria

1. WHEN 用户查看手动配置模型时，自助查询页 SHALL 按账号分组展示可复制模型。
2. WHEN 用户打开 AI 配置模型选择器时，系统 SHALL 显示每个模型的账号名称和账号色。
3. WHEN 用户打开 CC Switch 导入对话框时，系统 SHALL 在模型选项文本中显示账号名称。
4. WHEN 用户打开 VSCode 导入对话框时，系统 SHALL 显示每个模型的账号名称和账号色。
5. WHEN 用户复制 AI 配置说明时，系统 SHALL 在模型明细中写入所属账号名称。

### Requirement 5: 保持现有操作能力

**User Story:** AS 自助查询用户, I want 保留现有复制和导入能力, so that 账号归属展示不会改变配置流程。

#### Acceptance Criteria

1. WHEN 用户点击手动配置中的模型标签时，系统 SHALL 复制公开模型 ID。
2. WHEN 用户选择模型并生成导入配置时，系统 SHALL 使用公开模型 ID 生成配置。
3. WHEN 自助查询接口返回旧格式数据时，前端 SHALL 使用现有模型数组展示兼容视图。
4. WHEN 模型列表为空时，自助查询页 SHALL 显示现有的获取模型提示。
