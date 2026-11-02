# Requirements Document

## Introduction

人像、姿势、服装三个参考库支持管理员添加自己的参考图。本地参考与上游参考一起检索和展示，上游同步不覆盖本地数据。

## Glossary

- **参考库**: 人像参考库、姿势参考库或服装参考库。
- **上游参考**: 从外部图库同步并缓存的只读参考记录。
- **本地参考**: 管理员上传并保存在本站数据库和文件目录中的参考记录。
- **参考字段**: 标题、分组、分类、细节、标签、提示词和 18+ 标记。
- **管理权限**: 当前参考库已有的同步权限。

## Requirements

### Requirement 1: 添加本地参考

**User Story:** AS 参考库管理员, I want 上传自己的参考图, so that 生成时可以复用站内素材。

#### Acceptance Criteria

1. WHEN 管理员具备目标参考库的管理权限, THE 参考库页面 SHALL 提供添加参考入口。
2. WHEN 管理员提交图片、标题和目标参考库字段, THE 系统 SHALL 创建一条本地参考。
3. THE 系统 SHALL 接受 JPEG、PNG、WebP 和 GIF 图片，并拒绝超过 15 MB 的文件。
4. IF 标题为空或图片缺失, THE 系统 SHALL 拒绝创建并说明缺失字段。

### Requirement 2: 联合检索

**User Story:** AS 参考库使用者, I want 在同一列表查找上游和本地参考, so that 选择参考时不需要切换来源。

#### Acceptance Criteria

1. WHEN 使用者检索参考库, THE 系统 SHALL 在同一结果集返回匹配的本地参考和上游参考。
2. THE 系统 SHALL 将本地参考排在同一页结果中的上游参考之前。
3. WHEN 本地参考匹配分组、分类、标签、关键词或 18+ 条件, THE 系统 SHALL 按与上游参考相同的规则过滤。
4. THE 参考结果 SHALL 标明记录来源为本地或上游。

### Requirement 3: 删除与隔离

**User Story:** AS 参考库管理员, I want 删除错误的本地参考, so that 参考库保持可用。

#### Acceptance Criteria

1. WHEN 管理员删除本地参考, THE 系统 SHALL 移除记录及其图片文件。
2. IF 目标记录属于上游参考, THE 系统 SHALL 拒绝删除。
3. WHEN 管理员同步上游参考库, THE 系统 SHALL 保留全部本地参考及其文件。
4. IF 管理员缺少目标参考库管理权限, THE 系统 SHALL 拒绝添加和删除。

### Requirement 4: 复用本地参考

**User Story:** AS 图片生成管理员, I want 把本地参考用于生成, so that 新素材可以进入现有生成流程。

#### Acceptance Criteria

1. WHEN 使用者选择本地参考, THE 图片生成页 SHALL 读取该参考的站内图片地址和提示词。
2. THE 本地参考图片地址 SHALL 由本站文件访问路径提供。
3. WHEN 本地参考被标记为 18+, THE 系统 SHALL 在关闭 18+ 显示时同时隐藏列表和详情。
