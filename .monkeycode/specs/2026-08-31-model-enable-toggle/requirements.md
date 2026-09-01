# Requirements Document

## Introduction

管理员需要按上游账号或网关代理路由关闭个别模型，避免用户使用过贵或不可用的模型。关闭后的模型不再出现在网关 `/v1/models` 与自助查询结果中，但测速页仍可选择并测试这些模型，且测速失败或超时支持一键关闭。

## Glossary

- **上游账号**：管理后台「上游账号」页中的 `UpstreamAccount`
- **网关代理**：管理后台「网关代理」页中的本地 Agent 路由账号
- **公开模型目录**：Key 绑定账号后对外暴露的模型列表（`/v1/models`、自助查询）
- **测速**：管理后台「模型测速」页对指定账号模型发起的探测请求

## Requirements

### Requirement 1

**User Story:** AS 管理员, I want 在上游账号页按模型启用或关闭, so that 过贵或不可用的模型不再对外提供

#### Acceptance Criteria

1. WHEN 管理员在上游账号页关闭某个已入库模型, THE 系统 SHALL 将该模型标记为关闭并在刷新模型列表后保留该标记
2. WHEN 管理员在上游账号页重新启用某个已关闭模型, THE 系统 SHALL 将该模型恢复为对外可用
3. WHILE 模型处于关闭状态, THE 上游账号页 SHALL 仍展示该模型并标明已关闭

### Requirement 2

**User Story:** AS 管理员, I want 在网关代理路由上按模型启用或关闭, so that 本地代理暴露的模型也能单独停用

#### Acceptance Criteria

1. WHEN 管理员在网关代理详情页关闭某条路由下的模型, THE 系统 SHALL 将该模型标记为关闭
2. WHEN 管理员重新启用该模型, THE 系统 SHALL 将该模型恢复为对外可用
3. WHILE 模型处于关闭状态, THE 网关代理详情页 SHALL 仍展示该模型并标明已关闭

### Requirement 3

**User Story:** AS 使用网关的客户端, I want 只看到已启用的模型, so that 不会选到被管理员关闭的模型

#### Acceptance Criteria

1. WHEN 客户端请求 `/v1/models`, THE 系统 SHALL 只返回已启用模型
2. WHEN 客户端使用已关闭模型发起对话, THE 系统 SHALL 拒绝该请求并说明模型已关闭
3. WHEN 用户在自助查询页查询 Key, THE 系统 SHALL 只展示已启用模型

### Requirement 4

**User Story:** AS 管理员, I want 测速页仍能测试已关闭模型, so that 关闭后仍可验证模型是否恢复可用

#### Acceptance Criteria

1. WHILE 模型已关闭, THE 测速页 SHALL 仍列出该模型并允许选中测速
2. WHEN 测速结果为失败或超时, THE 测速页 SHALL 提供一键关闭该模型的操作
3. WHEN 管理员在测速页一键关闭模型, THE 系统 SHALL 将该模型标记为关闭，且测速页仍可继续对该模型测速
