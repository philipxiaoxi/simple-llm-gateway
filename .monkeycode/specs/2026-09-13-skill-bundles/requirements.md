# Requirements Document

## Introduction

在现有 Skills 仓库之上，新增「Skills 组合」能力：管理员可将系统中已入库的多个 Skill 编成命名组合包，并支持组合包的增删改查、成员添加/移除、整包 zip 下载，以及复制给 AI 的短期 token 安装指令。入口为 Skills 列表页的二级页面，带返回导航。

## Glossary

- **Skill**：系统已入库的单个 Agent Skill 记录（含 `SKILL.md` 与附属文件）。
- **Skills 组合 / 组合包（Skill Bundle）**：命名后的 Skill 集合，成员仅引用已入库 Skill，不复制磁盘文件。
- **成员（Member）**：组合包与 Skill 的关联项；同一 Skill 可出现在多个组合包中。
- **系统**：本网关管理后台（管理员鉴权）及公开 token 下载接口。
- **安装指令**：复制到剪贴板的文案，含 5 分钟有效下载链接与 AI 安装步骤。

## Requirements

### Requirement 1: 二级页面入口与导航

**User Story:** AS 管理员, I want 从 Skills 列表进入独立的「Skills 组合」页面并一键返回, so that 组合管理与单 Skill 管理分区清晰。

#### Acceptance Criteria

1. WHEN 管理员打开 Skills 列表页, THE 系统 SHALL 展示可点击入口（按钮或等价控件），文案标明「Skills 组合」。
2. WHEN 管理员点击该入口, THE 系统 SHALL 导航至独立路由（建议 `/skills/bundles`），并渲染组合包列表为主内容。
3. WHEN 管理员位于组合相关页面, THE 系统 SHALL 在左上角提供返回控件；点击后返回 Skills 列表页（`/skills`）。
4. WHILE 管理员处于组合包详情页, WHEN 点击左上角返回, THE 系统 SHALL 回到组合包列表页；WHILE 管理员处于组合包列表页, WHEN 点击左上角返回, THE 系统 SHALL 回到 Skills 列表页。

### Requirement 2: 组合包列表与查询

**User Story:** AS 管理员, I want 浏览与搜索组合包, so that 快速定位目标合集。

#### Acceptance Criteria

1. WHEN 管理员进入「Skills 组合」页, THE 系统 SHALL 展示组合包列表，每项至少包含：名称、描述（可空）、成员数量、更新时间。
2. WHEN 管理员输入关键词并触发搜索, THE 系统 SHALL 按名称与描述对组合包做不区分大小写的子串匹配过滤。
3. WHEN 当前无任何组合包, THE 系统 SHALL 展示空状态提示，并提供创建组合包的入口。
4. WHEN 列表加载失败, THE 系统 SHALL 展示错误信息与重试入口。

### Requirement 3: 组合包创建与编辑元数据

**User Story:** AS 管理员, I want 创建并修改组合包的名称与描述, so that 合集可被识别与维护。

#### Acceptance Criteria

1. WHEN 管理员提交创建表单且名称非空, THE 系统 SHALL 创建组合包并返回列表可见的新记录。
2. WHEN 管理员提交的名称为空或仅空白, THE 系统 SHALL 拒绝创建并提示名称必填。
3. WHEN 管理员修改已有组合包的名称或描述并保存, THE 系统 SHALL 持久化变更并更新 `updated_at`。
4. THE 系统 SHALL 允许名称在系统内重复（首版不强制唯一）；若后续产品要求唯一，另开需求。
5. THE 系统 SHALL 将名称长度限制在 128 字符以内，描述长度限制在 2000 字符以内（与 Skill 元数据量级对齐）。

### Requirement 4: 组合包删除

**User Story:** AS 管理员, I want 删除不再需要的组合包, so that 列表保持精简。

#### Acceptance Criteria

1. WHEN 管理员确认删除某组合包, THE 系统 SHALL 删除该组合包及其全部成员关联。
2. WHEN 组合包被删除, THE 系统 SHALL 保持被引用 Skill 本体与磁盘文件不变。
3. WHEN 管理员取消删除确认, THE 系统 SHALL 保留该组合包。

### Requirement 5: 成员添加与移除

**User Story:** AS 管理员, I want 向组合包添加或移除已入库 Skill, so that 合集内容可编排。

#### Acceptance Criteria

1. WHEN 管理员在组合包中发起添加成员, THE 系统 SHALL 仅展示系统已入库的 Skill 作为可选来源（支持按名称/slug/描述搜索）。
2. WHEN 管理员选择一个或多个尚未属于该组合包的 Skill 并确认, THE 系统 SHALL 将这些 Skill 加入该组合包成员列表。
3. IF 管理员尝试添加已在该组合包中的 Skill, THE 系统 SHALL 跳过重复项并在结果中体现跳过原因或静默去重（实现二选一，须在 UI 上可理解）。
4. WHEN 管理员从组合包移除某成员, THE 系统 SHALL 仅解除关联，不删除 Skill 本体。
5. THE 系统 SHALL 禁止通过上传文件或外部 URL 直接向组合包注入未入库 Skill；成员来源仅限已入库记录。
6. WHEN 组合包内某成员对应的 Skill 已被系统删除, THE 系统 SHALL 在成员列表中展示为失效项，并允许管理员移除该失效关联；打包下载时跳过失效成员。

### Requirement 6: 整包 zip 下载

**User Story:** AS 管理员, I want 将组合包内全部有效 Skill 打成一个 zip 下载, so that 可本地分发或备份。

#### Acceptance Criteria

1. WHEN 管理员触发组合包「打包下载」且存在至少一个有效成员, THE 系统 SHALL 返回 `application/zip`；zip 内**每个有效 Skill 独占一个顶层目录**，目录名为该 Skill 的 `slug`，目录下为该 Skill 的完整文件树（与单 Skill `build_skill_zip` 的 `{slug}/...` 结构一致，多 Skill 时并列多个顶层目录）。
2. WHEN 多个成员 slug 在 zip 顶层冲突（Skill.slug 全局唯一，作为防护）, THE 系统 SHALL 保证 zip 内顶层目录不互相覆盖。
3. IF 组合包没有任何有效成员, THE 系统 SHALL 拒绝下载并提示组合包为空或无可打包成员。
4. WHEN 管理员已登录后台, THE 系统 SHALL 允许经鉴权的打包下载接口完成下载。
5. THE 系统 SHALL 不在 zip 根目录额外包裹一层组合包名称目录（根下直接是各个 `{slug}/`）。

### Requirement 7: 复制 AI 安装指令（短期 token）

**User Story:** AS 管理员, I want 复制一段带短期下载链接的安装指令给 AI, so that AI 可免登录拉取组合包 zip 并安装到本机 Agent skills 目录。

#### Acceptance Criteria

1. WHEN 管理员点击「复制安装指令」, THE 系统 SHALL 签发有效期为 300 秒的下载 token，并生成包含绝对下载 URL 与安装步骤的文案写入剪贴板。
2. THE 系统 SHALL 使用 JWT（HS256，密钥为应用 `app_secret_key`），payload 至少包含：`scope`（专用于组合包下载）、`bundle_id`、`sub`（签发管理员用户名）、`ver`（管理员 `token_version`）、`iat`、`exp`。
3. WHEN 持有有效 token 的客户端请求公开下载接口, THE 系统 SHALL 在无管理员 Bearer 的情况下返回与鉴权打包下载等价的 zip。
4. IF token 过期、签名无效、`scope` 不匹配、`bundle_id` 不匹配、管理员不存在或 `token_version` 不一致, THE 系统 SHALL 拒绝下载并返回 401 或 403。
5. THE 安装指令文案 SHALL 说明链接 5 分钟内有效，并指导 AI：下载 zip → 解压 → 将各 `{slug}/` 安装到本机 Agent skills 根目录下对应子目录（示例路径可写 `~/.claude/skills/`，并注明由 AI 按实际 Agent 目录判断）。
6. IF 组合包无有效成员, THE 系统 SHALL 拒绝签发下载链接或在复制时提示无法生成。

### Requirement 8: 权限与安全

**User Story:** AS 系统管理员, I want 组合包管理仅限已登录管理员，公开接口仅限短期 token, so that 仓库不被未授权改写或枚举。

#### Acceptance Criteria

1. WHEN 未登录客户端请求组合包的创建、修改、删除、成员变更或鉴权下载, THE 系统 SHALL 返回 401。
2. THE 系统 SHALL 将公开组合包下载限制在带 token 的专用路由上，不暴露无 token 的列举或元数据接口给匿名用户（首版公开面仅下载 zip）。
3. THE 系统 SHALL 在管理员变更密码导致 `token_version` 递增后，使既有组合包下载 token 全部失效。

### Requirement 9: 展示与可用性（PC / 移动端）

**User Story:** AS 管理员, I want 在桌面与窄屏下均可完成组合管理, so that 移动端后台也可用。

#### Acceptance Criteria

1. THE 组合列表与详情布局 SHALL 在窄屏下可纵向浏览，主要操作按钮可 wrap，避免强制横向页面滚动。
2. WHEN 组合名称或成员 Skill 名称过长, THE 系统 SHALL 截断或换行显示，并用 `title` 或等价方式保留完整文本可访问性。
3. THE 成员添加交互 SHALL 支持搜索过滤，避免仅靠超长无筛选列表点选。

### Requirement 10: 非目标（首版明确不做）

**User Story:** AS 产品负责人, I want 首版范围边界清晰, so that 实现不膨胀。

#### Acceptance Criteria

1. THE 首版 SHALL 不包含：组合包之间的嵌套、版本发布流、分享给非管理员的长期公开页、按组合包自动同步远端 Git、成员排序拖拽（若实现固定为加入顺序或 id 序即可）、从组合包反向「一键安装到某个上游」。
2. THE 首版 SHALL 不在组合包内存储 Skill 文件副本；成员仅为引用。
3. THE 首版 SHALL 不提供匿名创建/修改组合包。

## Resolved Decisions

1. **页面结构**：列表页 + 详情页（详情内管理成员、下载、复制安装指令）。
2. **删除组合包**：仅删除组合包记录与成员关联，Skill 本体与磁盘文件保留。
3. **安装指令目标**：示例路径 `~/.claude/skills/`，由 AI 按本机实际 Agent skills 目录判断；不写死仅 Claude Code。
4. **Zip 结构**：每个 Skill 一个顶层目录 `{slug}/...`，多 Skill 并列，根下无额外组合包外壳目录。
