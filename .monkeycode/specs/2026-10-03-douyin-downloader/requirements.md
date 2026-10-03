# Requirements Document

## Introduction

在 MCP 广场新增能力 `douyin`（抖音视频下载）：接收抖音分享文案或直链，解析出作品元数据与媒体（视频、图集图片），把媒体转存到平台数据卷，并对外提供一个稳定的、可直接分发的下载地址。能力同时注册进现有 REST 与 MCP 协议面，并复用能力平面的 MCP Key、能力白名单与调用日志。

媒体转存而非仅返回抖音 CDN 直链，原因是直链带时效签名、可能受地域与风控限制，无法作为稳定交付物。转存后由平台统一提供下载地址、保留期清理与访问控制。

解析统一走 TikHub 托管解析 API（管理页配置 Base URL 与加密存储的 API Key，环境变量兜底），不再内置自研解析，也不在本地安装任何解析依赖。抖音对服务端请求有 WAF/签名拦截，平台侧反爬由 TikHub 处理，平台不实现任何风控绕过。

合规边界：本能力用于用户本人对公开作品的个人合规下载与留存，不提供去水印绕过、批量抓取、反风控或规避平台限制的手段，不改变作品的版权归属。

## Glossary

- **能力标识（capability_id）**：注册表内稳定标识 `douyin`，MCP 工具名前缀 `douyin_`。
- **分享文案（share_text）**：包含抖音短链或直链的任意文本，例如“5.84 复制打开抖音… https://v.douyin.com/xxxx/ …”。
- **直链（direct_url）**：抖音作品地址，形如 `https://www.douyin.com/video/{aweme_id}`、`https://www.iesdouyin.com/share/video/{aweme_id}/` 或 `v.douyin.com` 短链。
- **aweme_id**：抖音作品唯一标识，数字串。
- **媒体项（Media Item）**：一次解析得到的单个可下载对象，`kind` 为 `video` / `image` / `audio`。
- **图集（Gallery）**：以多张图片为主体的作品，媒体项为多张 `image`。
- **解析任务（Job）**：一次分享文案/直链的解析与转存作业，聚合若干媒体项。
- **转存（Rehost）**：平台从抖音 CDN 拉取媒体字节并落盘为自有文件。
- **下载地址（Download URL）**：`/v1/douyin/media/{media_id}` 相对路径，含签名令牌后可直接在浏览器下载（按当前 origin 解析）；另提供 `{APP_BASE_URL}/v1/douyin/media/{media_id}` 绝对地址供脚本使用。
- **签名令牌（Signed Token）**：HMAC 签名、带过期时间的下载凭证，绑定 `media_id`。
- **提取器（Extractor）**：把直链解析为元数据与媒体下载地址的适配器实现；本能力固定为 TikHub 托管 API。
- **TikHub 解析配置（Provider Config）**：全局单例的 TikHub Base URL 与 API Key，管理页可写入 / 清除，未配置时回退环境变量。
- **能力平面**：现有 `capabilities/` 注册、鉴权、日志体系。

## Requirements

### Requirement 1: 能力注册与授权

**User Story:** AS 平台管理员, I WANT `douyin` 出现在 MCP 广场并按 Key 授权, SO THAT 下游只能调用被允许的下载能力。

#### Acceptance Criteria

1. THE MCP 广场 SHALL 在服务目录展示已启用的 `douyin` 能力，包含名称、说明、版本、分类与管理入口 `/mcp-plaza/douyin`。
2. WHEN 管理员为 MCP Key 勾选 `douyin`, THE 系统 SHALL 允许该 Key 调用抖音下载的 REST 与 MCP 接口。
3. IF MCP Key 未授权 `douyin`, THE 系统 SHALL 拒绝该 Key 的所有抖音下载调用并返回 403。
4. IF 请求携带聊天 `sk-` 密钥访问抖音下载协议接口, THE 系统 SHALL 返回 401。
5. THE 系统 SHALL 把每次抖音下载协议调用写入既有 MCP 调用日志，记录能力标识、操作名、成功或失败、耗时与错误信息。

### Requirement 2: 输入解析与域名白名单

**User Story:** AS 集成方, I WANT 直接粘贴分享文案即可解析, SO THAT 我不用手工从文案里抠链接。

#### Acceptance Criteria

1. WHEN 输入为分享文案, THE 系统 SHALL 从文案中提取首个抖音链接作为解析目标。
2. WHEN 输入为直链, THE 系统 SHALL 直接以其作为解析目标。
3. IF 输入中不存在可识别的抖音链接, THE 系统 SHALL 返回 400 并说明未找到链接。
4. THE 系统 SHALL 只接受主机名属于抖音白名单的链接，白名单包含 `douyin.com`、`v.douyin.com`、`www.douyin.com`、`www.iesdouyin.com`、`iesdouyin.com` 及其子域。
5. WHEN 解析过程中发生重定向, THE 系统 SHALL 校验每一跳的目标主机仍属于白名单，否则中止并返回 400。
6. THE 系统 SHALL 拒绝解析目标解析到回环、私有、链路本地或保留 IP 的链接。

### Requirement 3: 元数据与媒体解析

**User Story:** AS 使用者, I WANT 解析出作品信息与可下载媒体, SO THAT 我能确认拿到的是目标作品。

#### Acceptance Criteria

1. WHEN 解析成功, THE 系统 SHALL 返回 `aweme_id`、标题或文案、作者昵称与作者 ID。
2. WHEN 作品含视频, THE 系统 SHALL 产出一个 `kind=video` 的媒体项，并返回时长、封面地址与视频直链。
3. WHEN 作品为图集, THE 系统 SHALL 产出与图片数量一致、`kind=image` 的媒体项，并按展示顺序编号。
4. WHEN 作品含音频, THE 系统 SHALL 在可获得时产出 `kind=audio` 的媒体项。
5. IF 一次作品产出的媒体项数量超过上限, THE 系统 SHALL 拒绝该任务并返回 400。
6. IF 解析得到的作品已被删除、私密或不可访问, THE 系统 SHALL 返回 422 并说明作品不可用。
7. THE 系统 SHALL 在解析结果中标识实际使用的提取器名称。

### Requirement 4: 媒体转存与稳定下载地址

**User Story:** AS 使用者, I WANT 一个稳定、可直接打开的下载地址, SO THAT 我或下游系统随时能下载到文件。

#### Acceptance Criteria

1. WHEN 解析成功且需要转存, THE 系统 SHALL 从抖音 CDN 拉取媒体字节并落盘到数据卷。
2. WHEN 单个媒体项转存完成, THE 系统 SHALL 生成稳定的下载地址 `{APP_BASE_URL}/v1/douyin/media/{media_id}`。
3. WHEN 下载地址携带有效签名令牌, THE 系统 SHALL 返回对应媒体文件并附带正确的 `Content-Type` 与 `Content-Disposition` 文件名。
4. WHEN 下载地址未携带签名令牌但携带已授权且拥有该媒体的 MCP Key, THE 系统 SHALL 返回该媒体文件。
5. IF 下载地址既无有效签名令牌也无有效归属 Key, THE 系统 SHALL 返回 401。
6. IF 请求的媒体不属于请求者或已过期, THE 系统 SHALL 返回 404，不泄露存在性。
7. IF 媒体文件已被保留清理, THE 系统 SHALL 返回 410 并说明文件已过期。
8. THE 系统 SHALL 在转存结果中返回每个媒体项的原始直链与平台下载地址。
9. THE 系统 SHALL 支持声明 `rehost=false` 的仅解析模式，该模式下不落盘、只返回元数据与原始直链。

### Requirement 5: 转存校验与资源限制

**User Story:** AS 平台管理员, I WANT 转存被严格限流限大小, SO THAT 外部内容不会拖垮主机或占满磁盘。

#### Acceptance Criteria

1. THE 系统 SHALL 对单次转存设置总字节上限，超过上限时中止并返回 413。
2. THE 系统 SHALL 对单个媒体项设置字节上限，超过上限时将该媒体项置为失败并继续处理其余媒体项。
3. THE 系统 SHALL 对单次任务设置总超时，超时后中止剩余下载并把任务置为 `failed` 或部分成功。
4. THE 系统 SHALL 对转存响应校验 `Content-Type` 属于视频、图片或音频类别，否则拒绝落盘。
5. THE 系统 SHALL 在下载过程中按流式读取并累计字节数，禁止一次性读入内存。
6. THE 系统 SHALL 为每次任务分配独立目录，文件名只使用系统生成的安全名，禁止使用远程提供的文件名作为路径。
7. THE 系统 SHALL 限制同一 MCP Key 的并发转存任务数。

### Requirement 6: 异步任务与状态查询

**User Story:** AS 集成方, I WANT 转存过程可查询, SO THAT 大文件或图集不必阻塞调用。

#### Acceptance Criteria

1. WHEN 管理端或 REST 发起解析, THE 系统 SHALL 仅入库并在 `202` 响应中立即返回任务标识与 `queued` 状态，解析与下载均在后台执行，不阻塞提交请求。
2. THE 系统 SHALL 以阶段字段区分「解析中」（`resolving`）与「下载中」（`downloading`），并在下载阶段提供百分比、已下载字节与预期字节供轮询。
3. WHEN 全部媒体项转存完成, THE 系统 SHALL 将任务置为 `succeeded`、`percent=100` 并返回全部下载地址。
4. IF 全部媒体项转存失败, THE 系统 SHALL 将任务置为 `failed` 并保存错误信息。
5. IF 部分媒体项成功、部分失败, THE 系统 SHALL 将任务置为 `partial`，保留成功项并记录每个失败项的原因。
6. WHEN 进程重启, THE 系统 SHALL 把残留的非终态任务置为 `failed` 并提示重试。
7. WHEN 管理员对 `failed` 任务执行重试, THE 系统 SHALL 重新执行该任务且不改变其他任务。
8. WHEN 已授权 Key 调用 MCP 工具触发转存, THE 系统 SHALL 在单次调用内最多等待配置时长；超时则返回任务标识供后续用 `douyin_job` 轮询。

### Requirement 7: 管理页

**User Story:** AS 管理员, I WANT 在 MCP 广场粘贴分享文案并管理下载记录, SO THAT 我可以自助取值、预览与清理。

#### Acceptance Criteria

1. WHEN 管理员打开 `/mcp-plaza/douyin`, THE 页面 SHALL 展示任务列表，包含缩略封面、标题、作者、状态、媒体数、创建时间。
2. WHEN 管理员粘贴分享文案或直链并提交, THE 页面 SHALL 创建任务并按「解析中 → 下载中 → 完成」展示阶段步骤与下载进度（已下载/预期字节、百分比），完成后展示媒体项与下载地址。
3. WHEN 管理员点击媒体项的“复制地址”, THE 页面 SHALL 复制带域名与签名令牌的完整下载地址。
4. THE 媒体卡片 SHALL 默认不加载播放器，WHEN 管理员点击“预览”才按类型渲染视频、图片或音频，且音频预览不自动播放。
5. WHEN 管理员删除任务, THE 系统 SHALL 删除该任务的媒体文件与记录。
6. THE 页面 SHALL 在 PC 与移动端均可完成解析、查看、复制与删除；PC 保持信息密度，移动端避免控件挤压换行。
7. WHEN 管理员打开设置区, THE 页面 SHALL 展示 TikHub 配置状态（Base URL、是否已配置 Key、来源、更新时间），并提供保存与清除入口。
8. WHEN 管理员查看任务详情, THE 页面 SHALL 展示该任务实际使用的提取器名称。

### Requirement 8: 保留清理与生命周期

**User Story:** AS 平台管理员, I WANT 媒体按策略自动清理, SO THAT 磁盘不会被历史文件占满。

#### Acceptance Criteria

1. THE 系统 SHALL 按保留天数清理超过期限的媒体文件，并保留任务记录。
2. THE 系统 SHALL 对已清理的媒体标记为已清理，使下载返回 410 而不是 404。
3. WHEN 管理员删除任务, THE 系统 SHALL 同时删除媒体文件与媒体记录。
4. THE 系统 SHALL 在进程启动时清理上次异常退出遗留的临时文件。

### Requirement 9: TikHub 解析适配

**User Story:** AS 平台管理员, I WANT 解析固定走 TikHub 托管 API, SO THAT 无需本地依赖即可稳定解析抖音作品。

#### Acceptance Criteria

1. THE 系统 SHALL 使用 TikHub 托管 API 作为唯一解析来源，不再内置自研分享页解析，也不在本地安装解析依赖。
2. THE 系统 SHALL 提供 TikHub 适配器，调用 `GET /api/v1/hybrid/video_data` 解析作品并映射视频与图集。
3. WHEN TikHub 未配置 API Key, THE 系统 SHALL 返回 `extractor_unavailable` 并提示在管理页配置。
4. THE 系统 SHALL 通过环境变量控制 TikHub 超时、大小上限、保留天数与下载令牌有效期。
5. IF 抖音以 WAF/签名拦截，THE 系统 SHALL 通过 TikHub 上游错误返回 `upstream_error` 并附摘要。

### Requirement 10: TikHub 解析 API 配置

**User Story:** AS 平台管理员, I WANT 在管理页配置 TikHub 托管解析服务, SO THAT 无需本地依赖即可稳定解析抖音作品。

#### Acceptance Criteria

1. THE 系统 SHALL 提供管理接口与页面展示 TikHub 的 Base URL、是否已配置 API Key、来源与更新时间，且不返回 API Key 明文。
2. WHEN 管理员提交 Base URL 与 API Key, THE 系统 SHALL 以 `APP_SECRET_KEY` 派生的密钥加密存储 API Key，来源标记为 `page`。
3. WHEN 管理员仅提交 Base URL 而未提供 API Key, THE 系统 SHALL 保留已存储的 API Key 不变。
4. WHEN 未在管理页配置, THE 系统 SHALL 回退到环境变量 `DOUYIN_TIKHUB_BASE_URL` 与 `DOUYIN_TIKHUB_API_KEY`。
5. THE 系统 SHALL 允许管理员清除已保存的 TikHub 配置。
6. WHEN TikHub 返回 HTTP ≥ 400 或 `success=false`, THE 系统 SHALL 返回 `upstream_error` 并附上游错误摘要。
7. THE 系统 SHALL 对 TikHub 请求设置超时，超时返回 `upstream_error`，且不无限重试。
