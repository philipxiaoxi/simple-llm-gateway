# 概览页图表（每日调用 / Token / 模型占比 / 时段分布）

> 落地日期：2026-10-17　·　技术栈：ECharts 6（按需注册）+ React 19 + FastAPI 聚合接口
> 关联文档：[双端 UI 优化方案](./ui-ux-dual-end-optimization.md) / [概览页 `Dashboard.tsx`](../frontend/src/pages/Dashboard.tsx)

---

## 0. 结论摘要

概览页从「纯数字卡片」升级为「区间概览 + 4 张图表 + 账号消耗排行」，所有统计都由新增的单一聚合接口 `GET /api/admin/dashboard/stats` 提供，前端不再自己拼数据：

| 区块 | 内容 | 回答的问题 |
|---|---|---|
| 区间概览 | 请求、Token、成功率、平均耗时 + **环比上一等长区间** | 这周比上周忙还是闲？质量有没有掉？ |
| 每日调用次数 | 按模型堆叠柱状 + **可横向滑动的图例**（点击切换显示） | 哪天用了多少，主要是哪个模型贡献的？ |
| 每日 Token 消耗 | 输入 / 输出堆叠面积 + 合计与占比 | 成本趋势如何，上下文（输入）有没有膨胀？ |
| 模型占比 | 横向条形排行，可切换「次数 / Token」口径 | 钱和请求量集中在哪个模型？ |
| 时段分布 | 24 小时柱状（上海时区），峰值高亮 | 网关一天里什么时候最忙？ |
| 上游账号消耗 | 账号 × 次数 × Token + 占比条 | 流量压在哪条上游？该不该分流？ |

区间可切 **7 / 14 / 30 / 90 天**，选择记在 `localStorage`（`pivot:dashboard-days`）；没有数据时给空态和「查看近 90 天」的出口，不画空坐标系。

---

## 1. 后端：单一聚合接口

```
GET /api/admin/dashboard/stats?days=7   # days ∈ [1, 90]，默认 7
```

响应（`backend/app/schemas.py` → `DashboardStatsOut`）：

| 字段 | 说明 |
|---|---|
| `days / start_date / end_date` | 区间与上海自然日边界 |
| `daily[]` | 每天一行：`requests / failures / prompt_tokens / completion_tokens / tokens / model_counts`，**没有调用的日期补 0**，前端时间轴才连续 |
| `models[]` | 区间模型排行（最多 12 个 + 「其他」）：`requests / tokens / request_share` |
| `accounts[]` | 区间上游账号排行（最多 8 个） |
| `hourly[]` | 固定 24 项（上海时区 0–23 点） |
| `summary` | 区间汇总：请求、失败、成功率、Token 结构、`avg_latency_ms`、`p95_latency_ms`、活跃天数、活跃模型数 |
| `previous` | 上一个**等长**区间的同结构汇总，专供环比 |

实现要点（`backend/app/routers/admin_dashboard.py`）：

- 按上海自然日/小时分桶：`strftime('%Y-%m-%d', created_at, '+8 hours')`，与 `app/clock.py` 的时区约定一致（库里是 naive UTC）。
- Token 统计对历史数据做兜底：`coalesce(total_tokens, prompt_tokens + completion_tokens)`，旧日志不会被算成 0。
- 模型数超过 12 个时其余归入「其他」，避免图例无限增长；排行与堆叠柱共用同一份模型口径。
- SQLite 没有 percentile，p95 用「成功请求按 `latency_ms` 排序 + 偏移取分位」的近似实现（只取一行，不把延迟全量读进内存）。
- `days` 用 `Query(ge=1, le=90)` 校验，越界直接 422。

代价：一次请求约 8 条聚合查询，都带 `created_at` 区间过滤（该列有索引），7/30/90 天规模下均为毫秒级；前端用 React Query 缓存 30s，切区间时 `keepPreviousData` 保留旧数据避免整块闪骨架。

---

## 2. 前端结构

```
frontend/src/
├─ lib/chart-theme.ts             # 深色调色板、axis/tooltip 基础样式、tooltip HTML、面积渐变
├─ components/charts/echart.tsx   # ECharts 薄封装：按需注册、ResizeObserver、生命周期、aria-label
├─ components/charts/chart-card.tsx  # 标题/说明/右上操作 + 数据表兜底 + 空态 + 骨架
├─ components/charts/chart-legend.tsx # DOM 版横向滚动图例（点击切换系列）
├─ components/dashboard-summary.tsx   # 区间 KPI + 环比
├─ components/dashboard-charts.tsx    # 4 张图表 + 账号排行（懒加载入口）
└─ pages/Dashboard.tsx                # 区间切换、数据请求、图表懒加载
```

### 2.1 为什么换掉 ECharts 内置图例

ECharts 的 `legend.type='scroll'` 按「每页均分」分页，模型多时会把某一页最后一个图例**切在文字中间**（实测桌面端第 5.5 个 legend 只剩半截，移动端更明显）。改为 DOM 图例：

- 手机一行横向滑动（`overflow-x-auto` + 右侧渐隐提示），桌面用触控板/Shift+滚轮/滚动条滑动；
- 点击色块切换显示，状态通过 `legend.selected` 回传 ECharts —— 堆叠、坐标轴、tooltip 会自动重算；
- 有隐藏项时出现「全部显示」，`aria-pressed` 表达可见状态。

### 2.2 无障碍与兜底

- 每个图表容器 `role="img"` + 中文 `aria-label`（区间合计、峰值、模型拆分）；
- 每张卡底部「查看数据表」：`<details>` 折叠一张可横向滚动的语义表格，不依赖颜色/hover 也能读到精确数值；
- 图表不靠颜色单独编码信息：堆叠顺序固定、tooltip 带数值、表格兜底；
- 尊重 `prefers-reduced-motion`：命中时关闭图表动画。

### 2.3 双端行为

| 场景 | 行为 |
|---|---|
| 手机（<640） | 图表单列、高度 248px；图例单行横向滑动；卡片「查看数据表」可滚动 |
| 平板（640–1279） | 仍单列，模型占比/时段分布高度按模型条数计算（220–400px），两张卡底部对齐 |
| 桌面（≥1280） | 2 列网格：调用 + Token 一行、模型占比 + 时段分布一行、账号消耗通栏 |
| 侧栏折叠 / 横竖屏 | ECharts 容器 `ResizeObserver` 自动 `resize()`，无需重新初始化 |

---

## 3. 打包与性能

- 只注册用到的模块（`BarChart` / `LineChart` / `Grid` / `Tooltip` / `Legend` / `CanvasRenderer`），未引入 `echarts/index` 全量包。
- 图表区通过 `React.lazy` 动态导入，并在 `vite.config.ts` 里给 `echarts|zrender` 单独分块：**其它后台路由不再随主包加载图表库**。
- 构建产物（生产 build）：
  - `echarts-*.js` 约 533 KB（gzip 180 KB，brotli 148 KB）——仅概览页图表懒加载时请求；
  - `dashboard-charts-*.js` 约 9.9 KB；
  - 主 vendor 从引入前的约 900 KB 回落到约 369 KB。

---

## 4. 验证

- 后端：`pytest backend/tests/test_dashboard_stats.py`（3 例：上海日/小时分桶与补零、Token 兜底与上一区间环比、`days` 越界 422）+ `test_logs.py` 回归，共 9 passed。
- 前端：`npm run lint`、`npx tsc -b`、`npm run build` 全部通过。
- 浏览器实测（临时库 `/tmp/dashboard-demo.db` + 独立 8010 后端 / 5174 前端，未污染 `data/gateway.db`）：
  - 桌面 1440：2 列布局、图例横向滑动、点击图例隐藏 glm-5.2 后堆叠与坐标轴重算、「次数 / Token」口径切换、90 天稀疏数据正常、侧栏折叠后图表自适应；
  - 移动 390（iPhone 14 模拟）：KPI 两列、图表单列、图例单行滑动带右侧渐隐、`1/3`→滑动翻页、数据表可读；
  - 真实库 7 天无数据：空态 + 「查看近 90 天」按钮，切 90 天后正常展示历史数据。

---

## 5. 后续可做（未做）

- 图表时间粒度切换（日 / 周 / 月）：当前按日分桶，90 天柱状已较密；
- 模型维度加「token/请求」效率指标与异常模型高亮；
- 失败原因分类（按 `http_status` / `error_message` 聚合）单独成图；
- 图表导出（PNG / CSV）与自定义时间范围（当前固定 7/14/30/90）。
