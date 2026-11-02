# Requirements Document

## Introduction

视频生成页提供「一键提示词优化」。系统使用管理员配置的上游账号和模型，按 MiniMax H3 提示词写作技能，把用户输入改写为适合视频模型的结构化提示词。技能正文随应用发布，运行时不访问外部技能仓库。

## Glossary

- **视频生成页**: 管理员编写视频提示词并提交生成任务的页面。
- **提示词优化器**: 使用已配置上游账号调用文本模型的后端服务。
- **优化配置**: 保存启用状态、上游账号和模型的站点级设置。
- **输入模式**: 根据参考图数量选择的改写模式，包括纯文本、首帧、首尾帧和尾帧。
- **优化结果**: 模型返回并经过结构校验的英文视频提示词。

## Requirements

### Requirement 1: 发起优化

**User Story:** AS 视频生成管理员, I want 一键优化当前提示词, so that 视频模型收到结构完整的提示词。

#### Acceptance Criteria

1. WHEN 视频提示词不为空且优化配置可用, THE 视频生成页 SHALL 允许管理员发起优化。
2. WHEN 管理员发起优化, THE 提示词优化器 SHALL 接收当前提示词、目标时长和参考图数量。
3. WHILE 优化请求未完成, THE 视频生成页 SHALL 保持原提示词可见并阻止重复提交。
4. WHEN 优化结果通过结构校验, THE 视频生成页 SHALL 用优化结果替换提示词输入框内容。

### Requirement 2: 选择改写模式

**User Story:** AS 视频生成管理员, I want 优化结果匹配参考图用途, so that 首帧和尾帧约束不会丢失。

#### Acceptance Criteria

1. WHEN 参考图数量为 0, THE 提示词优化器 SHALL 使用纯文本模式。
2. WHEN 参考图数量为 1, THE 提示词优化器 SHALL 使用首帧模式。
3. WHEN 参考图数量大于 1, THE 提示词优化器 SHALL 使用首尾帧模式。
4. WHERE 管理员显式选择尾帧模式, THE 提示词优化器 SHALL 使用尾帧模式。
5. THE 提示词优化器 SHALL 按所选模式要求对应的字段顺序和参考图标记。

### Requirement 3: 配置上游模型

**User Story:** AS 站点管理员, I want 选择用于优化的账号和模型, so that 优化调用使用指定上游。

#### Acceptance Criteria

1. THE 生成设置页 SHALL 提供优化开关、上游账号和模型配置。
2. WHEN 管理员保存优化配置, THE 系统 SHALL 持久化启用状态、账号标识和模型名称。
3. IF 所选账号不存在或不可用, THE 系统 SHALL 拒绝保存并保留原配置。
4. WHEN 优化开关关闭, THE 视频生成页 SHALL 隐藏优化入口。

### Requirement 4: 处理失败

**User Story:** AS 视频生成管理员, I want 优化失败时保留原文, so that 一次失败不会破坏已写内容。

#### Acceptance Criteria

1. IF 优化配置缺失、账号不可用或模型返回空内容, THE 提示词优化器 SHALL 返回明确失败原因。
2. IF 优化失败, THE 视频生成页 SHALL 保留原提示词并显示失败原因。
3. IF 返回内容缺少所选模式要求的字段, THE 提示词优化器 SHALL 拒绝替换原提示词。
4. THE 提示词优化器 SHALL 将优化结果限制在 12000 个字符以内。
