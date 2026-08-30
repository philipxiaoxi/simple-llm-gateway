# 需求实施计划

- [x] 1. 数据模型与词库落地
  - [x] 1.1 新增 `ContentAuditFinding`、`ContentAuditScan` 模型，并在 `init_db` 后可自动建表
    - 字段、唯一约束、严重级别与 `design.md` Data Models 一致
    - 覆盖 Requirement 2、3、6
  - [x] 1.2 运行时从 konsheng/Sensitive-lexicon 下载分类词库并缓存到数据目录
    - 缓存命中后不再下载；下载或加载失败时扫描器仍能跑 PII / 密钥
    - 覆盖 Requirement 4
  - [x] 1.3 为模型唯一约束与词库加载失败降级编写单元测试

- [x] 2. 实现三类检测器与文本抽取
  - [x] 2.1 实现消息正文抽取（字符串、多模态 text、tool_calls JSON）
    - 覆盖 Requirement 3.1
  - [x] 2.2 实现敏感词 Aho-Corasick 检测，命中带原库分类
    - 覆盖 Requirement 3.3、4
  - [x] 2.3 实现 PII 正则（手机、身份证校验位、邮箱、银行卡 Luhn）
    - 覆盖 Requirement 3.4
  - [x] 2.4 实现密钥正则（sk-/sk-ant-、Bearer、PEM、ghp_、AKIA）
    - 覆盖 Requirement 3.5
  - [x] 2.5 实现摘录截断、去重键、PII/密钥列表遮罩
    - 覆盖 Requirement 3.6、3.7、8.2、8.3
  - [x] 2.6 为检测器正反例、去重、摘录长度、遮罩编写单元测试

- [x] 3. 增量扫描服务
  - [x] 3.1 实现 `run_scan_batch`：按进度表挑选待扫记录，单轮批次上限，写命中并更新游标
    - 覆盖 Requirement 5.7、6.1、6.2、6.3、6.4
  - [x] 3.2 扫描失败隔离：单条消息损坏跳过并记 error，整轮异常向上抛给任务框架
    - 覆盖 Requirement 4.3、7.3
  - [x] 3.3 检查点 - 确保检测与扫描相关测试通过，如有疑问请询问用户

- [x] 4. 接入现有定时任务框架
  - [x] 4.1 注册循环任务 `content_audit`，默认 `interval_seconds=86400`
    - 修改 `jobs.py`、`job_settings.py`，`JOB_META` 文案按设计
    - 覆盖 Requirement 5.1、5.2、5.5
  - [x] 4.2 `_execute` 调用扫描批次，把 processed / new_findings / remaining / lexicon_ok 写入任务 extra
    - 覆盖 Requirement 5.6、7.1
  - [x] 4.3 忙碌拒绝与立即请求走现有 `/api/admin/jobs/{id}/run`
    - 覆盖 Requirement 5.3、5.4
  - [x] 4.4 为任务注册、默认间隔、409 忙碌编写测试

- [x] 5. 独立管理 API
  - [x] 5.1 新增 `admin_content_audit` 路由并在 `main.py` 挂载
    - `GET /api/admin/content-audit/findings` 分页与筛选
    - `GET /api/admin/content-audit/summary` 进度与分类计数
    - 覆盖 Requirement 2、7
  - [x] 5.2 列表摘录对 PII/密钥遮罩；未登录返回与其它管理接口一致的未授权
    - 覆盖 Requirement 2.5、8.1、8.2、8.3
  - [x] 5.3 为筛选、分页上限、401、遮罩编写 API 测试

- [x] 6. 管理端独立页面
  - [x] 6.1 增加 `/content-audit` 路由、侧栏「内容审计」、API 封装
    - 覆盖 Requirement 1.1、2
  - [x] 6.2 实现命中清单：状态条、筛选、PC 表格 / 移动卡片、空状态
    - 覆盖 Requirement 1.2、1.3、1.5、1.6、7.1、7.2
  - [x] 6.3 点击命中项跳转 `/logs/:id` 并高亮对应消息片段
    - 覆盖 Requirement 1.4、8.4
  - [x] 6.4 检查点 - 确保所有测试通过，如有疑问请询问用户
