# Requirements Document

## Introduction

模型测速页每次访问都需要从全部账号与模型中手动勾选测速目标，选择态无法保存且重复劳动。本特性引入「测速分组」：把一组常用测速目标命名保存到后端，测速页一键套用，并在测速历史中按分组归档与筛选，从而消除重复勾选、让历史会话可归组检索。

## Glossary

- **测速目标 (Target)**: 一个「账号 × 模型」组合，是测速可寻址的最小单位。
- **测速分组 (Group)**: 一组命名的测速目标集合，可被创建、重命名、编辑成员、删除。
- **测速会话 (Run)**: 在模型测速页点击「保存本次」后产生的一条历史记录。
- **可用成员 (Available Member)**: 其账号存在且状态为 active、且模型在账号模型清单中且未关闭的测速目标。
- **系统 (System)**: simple-llm-gateway 管理后台（后端服务与其前端页面）。

## Requirements

### Requirement 1

**User Story:** AS 管理员, I want 把当前已选测速目标保存为一个命名分组, so that 下次无需重新逐个勾选。

#### Acceptance Criteria

1. WHEN 管理员在模型测速页提交一个非空分组名称与至少一个测速目标, the 系统 SHALL 创建该分组并持久化其成员列表。
2. IF 分组名称去除首尾空白后为空, the 系统 SHALL 拒绝创建并返回校验错误。
3. IF 分组名称与已存在的分组名称重复, the 系统 SHALL 拒绝创建并保持已有分组不变。
4. WHEN 分组创建成功, the 系统 SHALL 在模型测速页的分组列表中立即可见该分组。
5. the 系统 SHALL 对每个分组记录创建时间与最近更新时间。

### Requirement 2

**User Story:** AS 管理员, I want 一键套用一个分组, so that 该分组的测速目标被自动选中。

#### Acceptance Criteria

1. WHEN 管理员对某个分组执行套用, the 系统 SHALL 把该分组的全部可用成员设为已选测速目标。
2. WHILE 分组已套用, the 系统 SHALL 在已选目标区展示成员并允许管理员手动增删。
3. WHEN 分组套用完成, the 系统 SHALL 以套用后的已选目标集合作为本次测速目标。
4. IF 所套用分组不含任何可用成员, the 系统 SHALL 提示该分组当前没有可用目标。

### Requirement 3

**User Story:** AS 管理员, I want 管理分组及其成员, so that 分组能随账号与模型变化保持有效。

#### Acceptance Criteria

1. WHEN 管理员编辑分组, the 系统 SHALL 允许向分组新增或从分组移除测速目标。
2. WHEN 管理员以当前已选目标更新分组, the 系统 SHALL 用当前已选目标整体替换该分组成员列表。
3. WHEN 管理员重命名分组, the 系统 SHALL 保留分组标识与成员列表不变。
4. WHEN 管理员删除分组, the 系统 SHALL 删除该分组并保留其关联的测速会话。
5. WHEN 分组成员或名称发生变更, the 系统 SHALL 更新该分组的最近更新时间。

### Requirement 4

**User Story:** AS 管理员, I want 分组自动清理已下架的账号与模型, so that 分组只保留仍可测速的目标。

#### Acceptance Criteria

1. WHEN 一个分组成员的账号已删除, the 系统 SHALL 在下一次读取分组时移除该成员。
2. WHEN 一个分组成员的模型已从账号模型清单中移除, the 系统 SHALL 在下一次读取分组时移除该成员。
3. WHILE 一个分组成员的模型仍在账号模型清单中但被关闭, the 系统 SHALL 保留该成员。
4. WHEN 管理员编辑成员后保存, the 系统 SHALL 以提交的成员列表替换该分组的成员。

### Requirement 5

**User Story:** AS 管理员, I want 在一个独立的分组管理页面维护每个分组要测速的模型, so that 我无需先在测速页勾选再保存。

#### Acceptance Criteria

1. WHEN 管理员创建一个仅含名称的分组, the 系统 SHALL 创建该空分组。
2. WHEN 管理员在分组管理页对某分组添加或移除模型并保存, the 系统 SHALL 以所选模型整体替换该分组成员。
3. the 系统 SHALL 在分组管理页允许重命名与删除分组。
4. WHEN 管理员删除分组, the 系统 SHALL 保留其关联的测速会话。

### Requirement 6

**User Story:** AS 管理员, I want 分组数据持久化在后端, so that 在不同浏览器与设备上都能复用同一套分组。

#### Acceptance Criteria

1. the 系统 SHALL 在后端持久化分组及其成员，且全部管理员共享同一份分组数据。
2. the 系统 SHALL 向管理员提供分组的创建、查询、更新与删除接口。

### Requirement 7

**User Story:** AS 管理员, I want 一键套用分组以及把当前选择保存为分组, so that 在测速页无需逐个勾选。

#### Acceptance Criteria

1. WHEN 管理员对某个分组执行套用, the 系统 SHALL 把该分组的全部可用成员设为已选测速目标。
2. WHEN 管理员把当前已选目标保存为新分组, the 系统 SHALL 以当前已选目标创建该分组。
3. IF 所套用分组不含任何可用成员, the 系统 SHALL 提示该分组当前没有可用目标。

### Requirement 8

**User Story:** AS 管理员, I want 保存测速会话时记录分组并可按分组筛选历史, so that 我能按分组检索历史会话。

#### Acceptance Criteria

1. WHEN 管理员保存测速会话时选定了分组, the 系统 SHALL 在测速会话上记录该分组。
2. WHEN 管理员在测速历史按分组筛选, the 系统 SHALL 仅展示属于该分组的测速会话。
3. WHEN 管理员查看某条测速会话, the 系统 SHALL 展示该会话关联的分组名称（若存在）。
