# 实施任务

## 后端：传输与缓存

- [x] 1. 新增 `app/static_assets.py`：`CachedStaticFiles`（预压缩直出 + 缓存头）与 `CompressTextMiddleware`（文本压缩、SSE 透传、q 值）
- [x] 2. `main.py` 挂载带缓存头的 `/assets`，图标 24h、`index.html`/`sw.js`/`manifest` 保持 `no-cache`
- [x] 3. 静态路由支持 HEAD（原来会落到 SPA 兜底），文件缺失返回 404 而不是 500
- [x] 4. 构建期生成 `.br`/`.gz`（`frontend/scripts/precompress.mjs`，无新增依赖）
- [x] 5. 启动预热数据库与模型目录，消除首个请求 13 倍冷启动开销

## 后端：查询与并发放大

- [x] 6. 排行榜/仪表盘只回看最近 30 次测速，不再全表扫描 `benchmark_results`
- [x] 7. 测速历史列表改用 SQL 聚合计数，去掉 `selectinload` 全量结果
- [x] 8. 记录列表用 `load_only`，不再读取正文与 reasoning 列
- [x] 9. 评估线程池方案并依据实测回退（5 并发下反而慢 2 倍，只保留查询范围收窄）
- [x] 10. 补索引：`request_logs(status, created_at)`、`(model)`、`(api_key_id, created_at, total_tokens)`、`skills(updated_at, id)`、`content_audit_findings(category)/(severity)`、`benchmark_results(ok, output_tokens_per_second)`

## 前端

- [x] 11. 路由级懒加载 + `Suspense`，Dashboard/Login 保持首屏内联
- [x] 12. `advancedChunks` 拆出 vendor chunk，业务改动不再让公共依赖失效
- [x] 13. 移除 Google Fonts 阻塞样式表，改为自托管 latin 子集 + `font-display: swap`
- [x] 14. 精简 SW 预缓存（图标移出、`index.html` 纳入、`navigateFallback` 打开）
- [x] 15. SW 注册推迟到 `load` 之后；React Query 设 `staleTime`/关闭 focus 重取
- [x] 16. 收敛轮询与重复 query key（Tools 空闲不再轮询、ContentAudit 空闲 60 s、`keyAccounts`/`keys` 合并）

## 验证

- [x] 17. `tests/test_static_assets.py`、`tests/test_spa.py`、`tests/test_benchmark.py` 覆盖新行为
- [x] 18. `pytest` 全绿（313 条）
- [x] 19. `scripts/perf_seed_scale.py` 造正式服规模（6156 日志/16489 命中）与增长规模（5 万日志/2 万测速结果）数据
- [x] 20. `scripts/perf_bench.py` 前后对比：静态资源体积、接口耗时与压缩收益
- [ ] 21. 线上灰度验证：首屏字节数、p95 与浏览器缓存命中率

## 后续（本次未做，按需排期）

- [ ] `/api/admin/accounts` 默认只返回模型数量，展开时再取明细（现在每次序列化全部模型）
- [ ] `/api/admin/skills` 把 `skill_md`/`analysis_json` 改为延迟加载并把过滤下推到 SQL
- [ ] 仪表盘的 12 个标量统计合并为一次聚合查询（数据量继续增长后收益才明显）
- [ ] 网关代理路径的凭据/模型目录缓存，减少每个转发请求的同步 DB 查询
