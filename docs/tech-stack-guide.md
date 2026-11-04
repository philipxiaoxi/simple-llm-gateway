# 技术栈采用指南

> 落地日期：2026-11-04　·　适用范围：`frontend/`、`backend/`
> 关联文档：[双端 UI 优化方案](./ui-ux-dual-end-optimization.md) / [概览页图表](./dashboard-charts.md) / [README 技术栈](../README.md#技术栈)

本文记录两端技术栈的**现状**与**后续引入规则**。新增依赖前先对照 §3 的判定条件，避免同类能力出现两套实现。

---

## 1. 现状一览

### 1.1 前端

| 能力 | 采用方案 | 版本 | 落地位置 |
|---|---|---|---|
| 框架 | React + Vite + TypeScript | 19.2 / 8.2 / 6.0 | `frontend/src` |
| 路由 | React Router | 7.18 | `src/App.tsx` |
| 样式 | Tailwind CSS 4（CSS 变量主题令牌） | 4.3 | `src/index.css` |
| UI 组件 | shadcn/ui（Radix + class-variance-authority） | Radix 1.3–2.4 / cva 0.7 | `src/components/ui/` |
| 图标 | lucide-react | 1.31 | 78 个文件 |
| 动画 | motion + tw-animate-css | 12.43 / 1.4 | `src/components/motion/`、弹层 |
| 服务端状态 | TanStack Query | 5.101 | 63 个文件 |
| 客户端状态 | React Context（`SessionProvider`）+ localStorage | — | `src/lib/session.tsx`、`src/lib/theme.ts`；localStorage 键 `pivot:theme-mode` / `pivot:theme` / `pivot:density` / `pivot:sidebar-collapsed` / `pivot:dashboard-days` |
| 图表 | ECharts（按需注册） | 6.1 | `src/components/charts/` |
| 时间 | dayjs（utc 插件） | 1.11 | `src/lib/utils.ts` |
| 播放器 | xgplayer | 3.0 | 抖音分享/详情页，动态导入 |
| 提示 | sonner | 2.0 | `src/lib/toast.ts` |

组件层统一从 `src/components/ui` 引入（`Button / Input / Card / Badge / Field / Select / Sheet / Dialog / Switch / IconButton / ActionSheet`），全站 85 处引入（83 个文件），只有 `ui.tsx` 自身直接引子模块。`ui.tsx` 是入口层：基础组件从 `src/components/ui/*.tsx` 再导出，`IconButton` 与 `ActionSheet` 因只在本层组合而直接实现于此。

### 1.2 后端

| 能力 | 采用方案 | 版本 |
|---|---|---|
| 运行时 | Python | 3.11 |
| Web 框架 | FastAPI + Uvicorn | 0.116 / 0.35 |
| 数据校验 | Pydantic + pydantic-settings | 2.11 / 2.14 |
| ORM | SQLAlchemy | 2.0 |
| 数据库 | SQLite（`DATABASE_PATH` → `sqlite:///`） | — |
| 迁移 | `db.py` 内的幂等建表 / `ALTER TABLE` 补列 | — |
| 协议转换 | LiteLLM | 1.97 |
| 认证 | JWT（PyJWT）+ bcrypt | 2.10 / 4.3 |
| HTTP 客户端 | httpx | 0.28 |
| 加解密 | cryptography（Fernet） | 45.0 |
| MCP | 官方 `mcp` SDK | 1.13 |
| 向量检索 | ChromaDB + pyahocorasick | 1.0 / 2.2 |
| 文档解析 | markitdown | 0.1.5 |
| 测试 / 静态检查 | pytest + ruff | 8.4 / 0.12 |

---

## 2. 未采用的库与原因

| 库 | 现状 | 现行替代 | 说明 |
|---|---|---|---|
| `zustand` | 未安装 | TanStack Query（服务端状态）+ Context（会话/权限）+ localStorage（主题、密度、侧栏、图表区间） | 现有跨组件状态都是只读派生或单点写入，Context 足够 |
| `@dnd-kit/*` | 未安装 | `Layout.tsx` 内基于 `setPointerCapture` 的约 50 行重排 | 唯一的重排场景是底部 Tab 排序，单列表、指针操作；当前没有键盘重排 |
| `react-resizable-panels` | 未安装 | CSS Grid（侧栏 72 / 240px，两栏内容区） | 当前没有「用户可拖分隔条」的需求 |
| `framer-motion` | 未安装 | `motion`（同一团队的新包名，API 一致） | 新代码统一用 `motion/react`，避免两个包共存 |

---

## 3. 引入判定条件

新增依赖前逐条自问，命中「引入」才动 `package.json`。

### 3.1 通用门槛

1. **现有能力可否覆盖**：先用现成工具实现，写清差在哪一步。
2. **注入体积**：单个依赖的 gzip 体积 ≤ 25 KB 可直接引入；25–80 KB 需要说明为何不可替代；> 80 KB 必须单独分块并记录到 §5。既有例外：`radix` 与 `motion` 的合计 gzip 已超过 25 KB（见 §5），靠独立分块与长期缓存消化。
3. **运行时影响**：是否引入新的全局副作用（事件监听、样式重置、SSR 不兼容）。移动端 320px 宽度必须无横向溢出。
4. **维护活跃度**：最近 12 个月有发布，且与 React 19 / Tailwind 4 兼容。
5. **是否成对出现**：同一能力只保留一套实现，旧方案在同一个 PR 内下线。

### 3.2 逐项触发条件

| 库 | 引入条件（满足任一） | 引入后必须做的事 |
|---|---|---|
| `zustand` | 出现**跨路由共享的可编辑草稿**（如「生成配置」在多个页面持续编辑并实时同步）；或同一份客户端状态被 3 个以上互不相干的组件树写入 | 只存客户端状态；服务端数据继续走 TanStack Query，禁止把 query 结果复制进 store |
| `@dnd-kit/core` + `sortable` | 需要**跨列表拖拽**、拖拽到列表外投放、或移动端长按拖拽并带自动滚动 | 替换 `Layout.tsx` 的手写实现，同一 PR 删除旧代码；同时补上现有实现缺失的键盘排序（`KeyboardSensor`）与 `aria-live` 播报 |
| `react-resizable-panels` | 页面需要**用户可拖动的分隔条**（如日志详情侧栏可拖宽、左右对比视图），且宽度需要持久化 | 面板尺寸写进 `localStorage`；提供双击复位；窄屏（< 1024px）自动退化为单列 |
| `motion` | 已在用 | 新动画只加在 `src/components/motion/` 或组件内的 `motion.*`；纯进出场优先用已有的 `tw-animate-css` 工具类 |
| 其他 UI 库（MUI / Ant Design / Chakra） | 无 | 继续用 shadcn/ui；缺组件用 `npx shadcn@latest add <name>` 生成到 `src/components/ui/` |

---

## 4. 具体接入方式

### 4.1 新增 shadcn/ui 组件

```bash
cd frontend
npx shadcn@latest add tooltip
```

- 生成位置由 `frontend/components.json` 的 `aliases.ui` 决定，落到 `src/components/ui/`。
- 主题令牌映射在 `src/index.css` 的 `@theme` 段：`--color-card` → `--color-panel`、`--color-border` → `--color-line`、`--color-primary` → `--color-signal`。新组件直接使用 `bg-card` / `border-border` / `text-foreground` 这些语义类，**不要**写 `dark:` / `light:` 变体。
- 需要在 `src/components/ui.tsx` 补一行再导出，页面侧继续从 `../components/ui` 引入，保持单一入口。
- CLI 可能顺带改写 `src/index.css`（注入默认 oklch 变量）。提交前 review diff，只保留组件文件与本项目需要的最小改动，避免覆盖现有令牌。

### 4.2 动画

| 场景 | 用哪个 | 写法 |
|---|---|---|
| 弹层、下拉的进出场 | `tw-animate-css` | `motion-safe:data-[state=open]:animate-in motion-safe:data-[state=open]:fade-in-0` |
| Toast 进出场 | sonner 内置 | 由 `sonner/dist/styles.css` 提供，无需额外类名 |
| 路由切换、区块入场、数值变化 | `motion` | 复用 `PageTransition` / `Reveal` / `ValuePop` |
| 悬停、颜色过渡 | Tailwind `transition-*` | 不引入 JS 动画 |

约束：

- 全部尊重 `prefers-reduced-motion`：
  - motion 组件用 `useReducedMotion()`；
  - `tw-animate-css` 的类加 **`motion-safe:`** 前缀（如 `motion-safe:data-[state=open]:animate-in`）。不要用 `motion-reduce:animate-none` 去覆盖：`data-[state=*]` 变体的选择器权重更高，覆盖不掉，动画仍会播放（已实测）；
  - 普通 Tailwind transition 加 `motion-reduce:transition-none` 即可；
  - sonner 自带 reduced-motion 规则；`index.css` 的 keyframes 在 `@media (prefers-reduced-motion: reduce)` 中关闭。
- 路由过渡只动 `opacity`。页面里有 `position: fixed` 元素，祖先加 `transform` / `filter` 会改变其定位参照。
- 需要列表增删动画时用 `AnimatePresence`，且同一页面只保留一个容器，避免多组退出动画互相等待。

### 4.3 Select 的写法约定

`Select` 基于 Radix，但保留原生写法的调用面（现 88 处调用 / 32 个文件）：

```tsx
<Select value={provider} onChange={(event) => setProvider(event.target.value)}>
  <option value="">全部供应商</option>
  <optgroup label="官方">
    <option value="deepseek">DeepSeek</option>
  </optgroup>
</Select>
```

- 子元素只用于解析选项，不渲染成真实 DOM。
- `value=""` 会映射为内部哨兵值，`onChange` 回调拿到的仍是 `''`。
- 选项 `value` 需要全局唯一，重复值会被丢弃（Radix 用 value 作选项标识）。
- 需要搜索或多选时，直接在页面里组合 `@radix-ui/react-select` 原语；`cmdk` 这类新依赖先按 §3 判定再引入。

### 4.4 后端新增能力

- 新表加在 `backend/app/models.py`，随 `Base.metadata.create_all` 自动创建；给已有表加列时在 `db.py` 追加幂等 `ALTER TABLE`，不引入 Alembic。
- 统计类接口遵循「定时任务写快照 + 请求只读」：参考 `services/dashboard_stats.py`、`services/mcp_stats.py`，任务注册在 `services/jobs.py`。
- 时间统一走 `app/clock.py`：库里存 naive UTC，分桶/展示按上海时区（`SHANGHAI_TZ`）。
- 新增依赖写进 `backend/requirements.txt` 并锁版本号，与现有 `==` 风格一致。

---

## 5. 体积预算

生产构建的公共块（gzip，2026-11 实测；数值取 `dist/assets/*.gz`，即实际传输体积）：

| 产物 | 体积 | 说明 |
|---|---|---|
| `vendor-*.js` | 109.7 KB | React、Router、Query 等三方常驻依赖 |
| `radix-*.js` | 34.3 KB | shadcn/ui 底层，独立分块便于长期缓存 |
| `motion-*.js` | 37.9 KB | 动画，独立分块 |
| `index-*.js` | 29.8 KB | 应用入口代码，每次发版都会变 |
| `index-*.css` | 20.6 KB | 含主题令牌与 `tw-animate-css` |
| `echarts-*.js` | 181.2 KB | 仅概览页懒加载时请求 |
| `xgplayer-*.js` | 76.5 KB | 仅公开分享/详情页动态加载 |

当前首屏合计 **232.3 KB**（前五项）。预算：

- 三方常驻（vendor + radix + motion）≤ 200 KB gzip，现为 181.9 KB；
- 首屏合计（含应用入口与 CSS）≤ 250 KB gzip，现为 232.3 KB。

规则：

- 只在少数页面用到的库，在 `vite.config.ts` 的 `codeSplitting.groups` 里单独成块，并配合 `React.lazy`。
- 提交前跑 `npm run build`，对比上表确认没有意外膨胀；超过预算先做依赖审计，再谈新增库。

---

## 6. 验收清单

前端改动合并前至少确认：

```bash
cd frontend
npx tsc -b
npx eslint src
npm run build
```

- 移动端 320 / 390 宽度下无横向溢出（`main.scrollWidth === main.clientWidth`）。
- 弹层在 320 / 390 宽度下无内部横向滚动；桌面端居中、移动端底部抽屉。
- 键盘可用：Tab 焦点不逃出弹层，Esc 关闭并把焦点还给触发元素。
- 深浅主题切换后，新增组件颜色跟随（`data-theme` 生效）。
- 命中 `prefers-reduced-motion` 时不播放动画（`getComputedStyle(dialog).animationName === 'none'`）。

后端改动合并前：

```bash
cd backend
python3 -m pytest tests -q
# 只检查本次改动的文件
python3 -m ruff check app/<改动目录> tests/<改动文件>
```

注意：仓库 ruff 基线尚未清零（主要是 `B008`——FastAPI 的 `Depends` / `Query` 出现在参数默认值里，属框架惯例）。把全仓 `ruff check app tests` 当门禁会一直失败，因此按改动文件检查，新增代码不要引入新的告警类型。

---

## 7. 复审触发点

出现以下任一情况时重新评估 §2 的「未采用」清单：

- 客户端需要跨路由共享可编辑状态 → 评估 `zustand`。
- 出现跨列表拖拽或移动端拖拽需求 → 评估 `@dnd-kit`。
- 出现用户可拖动的分栏需求 → 评估 `react-resizable-panels`。
- 首屏合计超过 250 KB gzip，或三方常驻超过 200 KB → 先做依赖审计，再谈新增库。
- 后端单实例写入成为瓶颈 → 评估多 worker 与缓存一致性方案（当前任务是进程内循环，见 `docs/perf/README.md`）。
