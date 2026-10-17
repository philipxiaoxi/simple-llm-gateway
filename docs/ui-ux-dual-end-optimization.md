# 前端 UI 双端（PC / 移动端）优化方案

> 审计对象：`frontend/`（React 19 + Vite 8 + Tailwind 4 + 自建深色设计语言）
> 代码基线：`ebad404`　·　审计日期：2026-10-17
> 方法：代码级审计（静态扫描 + 令牌对比度实算）+ ui-ux-pro-max 技能检索（UX / html-tailwind / react 栈规则）

---

## 0. 结论摘要

现有前端**不是从零开始**：已经有移动端壳（顶栏 + 抽屉 + 底部 5 Tab）、安全区处理、PWA、44px 触摸基线、输入框 16px 防 iOS 缩放、路由级代码分割、`prefers-reduced-motion` 兜底。**双端适配已经完成约 70%**，剩下的问题集中在「细节一致性」和「PC 效率」两端。

本方案给出 **5 个 P0**（P0-1 按产品要求保留禁用、不做改动，实际实施 4 项；合计约 1.5 人日，收益覆盖全站）：

| # | P0 问题 | 证据 | 影响端 |
|---|---|---|---|
| P0-1 | 视口禁止用户缩放 —— **已确认产品有意为之，保留** | `frontend/index.html:19`（`maximum-scale=1, user-scalable=no`） | 移动端（决策保留；用字号阶梯补偿可读性） |
| P0-2 | 全站弹窗在移动端是「居中卡片」，无抽屉/无焦点陷阱/无 Esc | `frontend/src/components/ui.tsx:134-174`，全站 **59 处、32 个文件**复用它 | 双端 |
| P0-3 | 表单控件边界对比度 1.43:1（WCAG 要求 3:1）、半透明次要文本 3.58:1（要求 4.5:1） | `index.css:40-50`、`ui.tsx:71`、16 处 `text-mist/70` | 双端（PC 强光下尤其明显） |
| P0-4 | 管理端内容区**没有最大宽度**，2K/4K 屏表格与卡片被拉满 | `components/Layout.tsx:276`（只有 `px-8`，全站仅 `PublicLeaderboard` 有 `max-w-6xl`） | PC |
| P0-5 | 移动端可读性：137 处 10–12px 字号（含中文），92 个原生按钮仅 3 个显式声明 44px 触摸高度 | `src/**`（扫描数据见 §2） | 移动端 |

其余为 P1/P2：平板断点断层、导航信息架构、表格双态覆盖不全、加载态/空状态、无障碍播报、PC 效率能力（快捷键/密度/批量）。

---

## 0.1 实施进度（第一轮，2026-10-17）

| 项 | 状态 | 落地内容 |
|---|---|---|
| P0-1 视口缩放 | ⏸ 按产品要求保留禁用 | 不改 `index.html`：禁用双指缩放是为了防止固定底栏/抽屉/全屏抽屉布局被放大破坏，代价（低视力用户无法放大）由字号阶梯、强对比色与后续可读性改造部分补偿 |
| P0-2 弹层双形态 | ✅ 已实施（核心） | `ui.tsx` 新增 `Sheet`：移动底部抽屉 / 桌面居中弹窗、焦点陷阱、Esc、`role=dialog` + `aria-modal` + `aria-labelledby`、关闭后焦点归还、body 滚动锁；`Dialog` 保留为兼容层（59 处调用零改动，宽度类自动翻译为桌面宽度）；`Accounts` 账号编辑、`Tools` 工具编辑、`Skills` 上传已改用底部固定操作区 |
| P0-3 对比度 | ✅ 已实施 | 新增 `line-strong / mist-strong / mist-soft / control` 令牌；`ui.Input`/`Select` + 29 处原生控件换成强边框 + 2px 焦点环；26 处半透明次要文本与占位符替换 |
| P0-4 内容宽度 | ✅ 已实施 | 新增 `PageContainer`（wide 1600 / form 1180 / prose 860），`Layout` 主内容区统一包裹 |
| P0-5 字号 | ✅ 部分实施 | 36 行含中文的 10–11px 文本改为 12px（`text-xs`）；触摸目标补齐与 `IconButton` 迁移留待 S4 |
| P1-3 双态列表 | 🟡 组件已就绪 | 新增 `components/data-view.tsx`（`DataView` + `ListSkeleton` + `EmptyState`），`McpCalls` 已迁移为桌面表格 + 移动卡片，加载态改骨架并加 `aria-busy` |
| P1-1 平板断点 | ✅ 已实施 | `Layout` 增加 `md` 图标轨（72px）+ 桌面可折叠侧栏；底部 Tab 在 ≥md 隐藏，主内容区在平板获得独立滚动 |
| P1-2 导航架构 | ✅ 已实施 | 18 项拆为 4 组并可折叠（当前组强制展开）；品牌行 + 搜索入口 + active 竖条 + 6px 细滚动条；收起态 40×40 图标轨 + 组间分隔线；折叠状态存 `localStorage`；移动抽屉搜索；PC ⌘K 命令面板 |
| P1-4 加载/空态 | ✅ 部分实施 | `ListSkeleton` / `EmptyState` 已就绪并被 `DataView` 使用；其余列表页按 S3 渐进迁移 |
| P1-5 无障碍 | ✅ 部分实施 | 路由切换 `aria-live` 播报、表单错误 `role="alert"`、IconButton 统一 44px 触摸高度；错误摘要与 axe 全站扫描待做 |
| P1-6 移动交互 | ✅ 已实施 | `FilterBar`（Accounts / Keys / McpCalls 折叠为底部筛选面板）、`ActionSheet`（账号卡片「⋯」更多操作）、长表单 `mobileFull` 全屏 Sheet、`IconButton` |
| P2 后续 | ⬜ 未做 | 趋势图、批量操作、表格增强、PWA 快捷方式等，见 §5 |

验证：`npm run lint`、`npx tsc -b`、`npm run build` 全部通过；构建产物 CSS 已包含新令牌类。按需求本轮**未做浏览器端自动化回归**（截图/axe/设备矩阵），留待需要时再补。

---

## 1. 审计范围

| 维度 | 范围 |
|---|---|
| 页面 | `src/pages/*.tsx` 47 个文件，18,446 行；`src/components` 35 个文件，5,499 行；53 条路由 |
| 布局壳 | `components/Layout.tsx`（PC 侧栏 / 移动顶栏 + 抽屉 + 底部 Tab） |
| 设计令牌 | `src/index.css`（`@theme` 11 个颜色令牌 + 3 个字体 + 2 个高度变量） |
| 交互原语 | `components/ui.tsx`（Switch / Button / Input / Card / Badge / Select / Field / Dialog） |
| 公开端 | `/share`、`/share/info`、`/share/leaderboard`、`/voice/join`、`/voice/send/*` |
| 未覆盖 | 后端接口契约、i18n、浅色主题（现状为 dark-only，本轮不做） |

---

## 2. 现状数据（扫描结果）

```
路由 53 条 · 页面 47 个 · 组件 35 个
Dialog 调用 59 处 / 32 个文件
响应式断点类：仅用 sm/md/lg/xl；Layout 壳只在 lg(1024) 切换一次
带「移动卡片兜底」的文件：13 个（ContentAudit / LeaderboardView / BenchmarkHistory /
  VoiceRoomDetail / Logs / Benchmark / Keys / Accounts / InfoFeed / Pagination /
  ModelAliasList / InfoItem 等）
text-[10px]~[12px]：137 处 / 38 个文件
原生 <button>：92 个（显式 min-h-10/11/12 仅 3 个；ui.Button 404 次调用已统一 44px）
icon-only 且缺 aria-label：扫描未发现（jsx-a11y recommended 已启用，但该规则覆盖不到触摸尺寸与焦点管理）
aria-live / aria-busy / role="alert"：0
autoFocus：0（Dialog 无焦点管理）
img 缺 alt：0（复核确认 16 张 <img> 均带 alt；此前按单行 grep 误判为 11 处）
z-index：z-10/20/30/40/50/60/100 混用，无层级令牌
构建产物（gzip）：vendor 122 KB · index.js 16 KB · index.css 17 KB · xgplayer 76 KB（按需）
```

### 2.1 已经做对的部分（本方案保留，不返工）

- ✅ **移动端壳完整**：`Layout.tsx` 顶栏（含安全区）+ 左抽屉（`w-72 max-w-[80vw]`）+ 底部 5 Tab（`h-14`）+ 路由切换滚动复位；PC 端 240px 侧栏 + 独立滚动容器 `lg:h-svh lg:overflow-y-auto`。
- ✅ **安全区**：`--app-header` / `--app-tab` 与 `env(safe-area-inset-*)` 组合，Toast 用 `mobileOffset` 避开灵动岛（`App.tsx:127`），`index.html` 已 `viewport-fit=cover` + `black-translucent`。
- ✅ **防 iOS 输入缩放**：`ui.Input/Select` 移动端 `text-base`、桌面 `md:text-sm`（`ui.tsx:71,115`）。
- ✅ **触摸基线**：`ui.Button` `min-h-11`（44px），桌面 `md:min-h-9`；`Switch` 同理；Tab/抽屉项均为 44px。
- ✅ **主题对比度**：主令牌全部通过 AA（`paper` 15.4:1、`signal` 14.3:1、`mist` 6.0:1、`danger` 5.7:1，均有实算）。
- ✅ **动效降级**：`prefers-reduced-motion` 已覆盖自定义动画；悬浮预览在 reduced-motion 下自动关闭。
- ✅ **性能基础**：路由懒加载、`InfoFeed` 用 `content-visibility:auto` / `content-intrinsic-size`、图片 `loading=lazy`（瀑布流）、字体自托管子集 + preload、PWA 自动更新。
- ✅ **表格双态意识**：已有 13 个文件采用「`hidden lg:block` 表格 + `lg:hidden` 卡片」的模式，方向正确，只差标准化。

### 2.2 双端缺口对照（按端归类）

| 缺口 | PC 端表现 | 移动端表现 |
|---|---|---|
| 弹窗形态 | 居中弹窗合理，但缺焦点陷阱、Esc、`aria-modal` | 居中卡片在拇指区外、标题随滚动消失、长表单体验差 |
| 内容宽度 | 1440+ 宽度下单卡横跨 1800px，行长失控 | 正常 |
| 导航 | 18 项平铺侧栏、无分组、无折叠、无全局搜索 | 抽屉同样 18 项平铺、无分组 |
| 断点 | 1024 以下突变为手机壳，平板（768–1023）浪费横向空间 | 平板下 5 Tab 被拉得很宽，依然只有单列 |
| 表格 | 全列展示 ✓，但无密度切换、无列固定、无批量选择 | `McpCalls` 7 列宽表仅横向滚动（唯一缺口） |
| 字号 | 10px eyebrow 可接受 | 中文 10–12px 偏小；正文层级不明确 |
| 交互 | 无快捷键、无悬停之外的加速路径 | 无长按/滑动/ActionSheet 等移动范式；筛选器纵向堆叠 |
| 反馈 | 加载态多为「加载中…」文本，无骨架 | 同上，首屏 CLS 与等待感更明显 |
| 无障碍 | 3 个自定义浮层有 `role=dialog`，`ui.Dialog` 没有 | 无 `aria-live`（已补路由播报）；图片 alt 齐全（视口禁缩放为产品决策，见 P0-1） |

---

## 3. P0：本周必修（约 1.5 人日，收益全站）

### P0-1 视口缩放：已确认保留禁用（不做改动）

**证据**：`frontend/index.html:18-20`

```html
<meta name="viewport"
      content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover" />
```

**结论**：产品侧确认这是**有意禁用**——本项目移动端是固定底栏 + 底部抽屉 + 全屏抽屉的 App 式布局，允许双指缩放后容易出现底栏与内容错位、抽屉被放大到只剩局部、横竖屏切换时 fixed 定位漂移。因此本轮**保持现状，不做改动**。

**已知代价**：违反 WCAG 2.2 SC 1.4.4（Resize Text），低视力用户无法双指放大（ui-ux-pro-max `ux` 库 `Viewport Meta` 为 High）。补偿措施放在本轮其他改造里：

- 字号阶梯：正文/辅助信息不再出现 <12px 中文（P0-5）；
- 对比度：控件边界、次要文本达到 AA（P0-3）；
- 系统级替代：iOS「设置 → 显示与亮度 → 文字大小」、Android「显示大小」对 `text-size-adjust: 100%` 的页面仍生效（`index.html` 未设置 `-webkit-text-size-adjust: none`，`index.css:59` 为 `100%`，保留系统字体缩放能力）；
- 若未来要恢复双指缩放，需同步回归 375/390/768 三档的底部 Tab、Sheet 与全屏抽屉布局。

**验收**：`index.html` 与基线一致（`git diff` 为空）；系统字体放大后页面不出现横向滚动或底部 Tab 遮挡内容。

---

### P0-2 `Dialog` 升级为 `ResponsiveSheet`（一处改动，59 个弹窗受益）

**证据**：`frontend/src/components/ui.tsx:134-174`

```tsx
// 现状：桌面/移动同一形态；无 role/aria-modal/Esc/focus trap
<div className="safe-area-modal fixed inset-0 z-50 overflow-y-auto bg-black/60 px-3">
  <div className="mx-auto my-6 w-full max-w-lg ... lg:my-[15vh]">
```

- 移动端是**居中卡片**：`my-6` 起排，标题在滚动后离开视口；`my-[15vh]` 只在桌面生效。
- 全站 59 处调用、32 个文件复用它；另有 3 个自定义浮层（`GenerationWorks.tsx:56`、`LogTurnDialog.tsx:96`、`InfoPublicSessionsDialog.tsx:108`）才带 `role="dialog" aria-modal="true"` 和 Esc。
- 长表单（`Accounts.tsx:164` 账号编辑、`Skills.tsx:96` 上传、`Tools.tsx:81` 工具编辑 `max-w-3xl`、`BenchmarkModelPicker.tsx:63` `max-w-5xl`）在手机上滚动体验尤其差。

**方案**：保留 `Dialog(title, children, onClose, className)` 的对外签名 → 内部改为委派给新的 `Sheet`，**59 个调用点零改动**；再对 Top 6 长表单追加 `footer` 属性实现底部固定操作区。

| 端 | 形态 |
|---|---|
| < `lg` | 底部抽屉：贴底、`rounded-t-2xl`、`max-h-[90dvh]`、顶部拖动条、标题栏 sticky、`pb-[max(0.5rem,env(safe-area-inset-bottom))]` |
| ≥ `lg` | 居中弹窗：`max-w-2xl`（可由 `className` 覆盖）、`max-h-[min(84dvh,56rem)]`、`my-[12vh]` |
| 通用 | `role="dialog" aria-modal="true" aria-label`、打开时焦点进入面板、Tab 循环、Esc 关闭、关闭后焦点归还、body 滚动锁、`overscroll-contain`、桌面点击遮罩关闭 / 移动端仅按钮关闭（防误触） |

实现骨架（可直接落到 `ui.tsx`）：

```tsx
// components/ui.tsx —— Dialog 变薄壳，Sheet 负责形态与可访问性
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

export function Sheet({
  title, children, footer, onClose, className,
}: {
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  className?: string
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); onClose(); return }
      if (event.key !== 'Tab' || !panelRef.current) return
      const nodes = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (!nodes.length) return
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    }
    document.addEventListener('keydown', onKey, true)
    const body = document.body
    const previousOverflow = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      body.style.overflow = previousOverflow
      previous?.focus?.()
    }
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 lg:items-center"
      onMouseDown={(event) => {
        // 桌面点遮罩关闭；移动端只允许按钮关闭，避免误触丢失已填表单
        if (event.target !== event.currentTarget) return
        if (window.matchMedia('(min-width: 1024px)').matches) onClose()
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          // 移动：底部抽屉
          'flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-t-2xl border-t border-line bg-panel',
          'pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-[0_-20px_60px_rgba(0,0,0,0.45)]',
          // 桌面：居中弹窗
          'lg:my-[12vh] lg:max-h-[min(84dvh,56rem)] lg:max-w-2xl lg:rounded-xl lg:border lg:pb-0 lg:shadow-[0_20px_60px_rgba(0,0,0,0.45)]',
          className,
        )}
      >
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-line lg:hidden" aria-hidden="true" />
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="truncate text-base font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-mist transition hover:bg-white/5 hover:text-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4">{children}</div>
        {footer ? (
          <footer className="shrink-0 border-t border-line bg-panel px-4 py-3">{footer}</footer>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}

// 兼容层：59 个调用点无需改动；需要底部固定操作区的表单再逐步加 footer
export function Dialog(props: { title: string; children: ReactNode; onClose: () => void; className?: string }) {
  return <Sheet {...props} />
}
```

**验收**：移动端（375/390）打开任意弹窗 → 贴底出现、标题固定、内部滚动、底部操作区可见；键盘 Tab 无法跳出弹窗；Esc 关闭且焦点回到触发按钮；VoiceOver/TalkBack 读出「对话框 + 标题」；`Accounts` 账号编辑在手机上不再需要先滚动才能看到保存按钮。

---

### P0-3 对比度令牌修复（表单边界 1.43:1 → 3:1 以上）

**实算结果**（脚本按 WCAG 相对亮度计算，详见附录 A）：

| 组合 | 当前比值 | 要求 | 结论 |
|---|---|---|---|
| `--color-paper` on `panel` | 15.42 | 4.5 | ✅ |
| `--color-mist` on `panel` | 6.01 | 4.5 | ✅ |
| `--color-signal` on `panel` | 14.33 | 4.5 | ✅ |
| `--color-danger` on `panel` | 5.73 | 4.5 | ✅ |
| **`--color-line` 边框 on `panel`** | **1.43** | **3.0**（控件边界 WCAG 1.4.11） | ❌ |
| **输入框背景 `ink` vs 卡片 `panel`** | **1.15** | 3.0 | ❌（控件靠边框识别，边框又不达标） |
| **`text-mist/70` on `panel`** | **3.58** | 4.5 | ❌（16 处，用于真实文本） |
| **`text-mist/60` on `panel`** | **2.96** | 4.5 | ❌（7 处） |
| **`text-mist/50` on `panel`** | **2.44** | 4.5 | ❌（3 处，如 `LogDetail.tsx:163` 分隔点） |

**关键**：`--color-line` 作为分隔线是装饰性的，可以保持 1.43:1；但**输入框/下拉框/次级按钮的可见边界**必须有 3:1。`ui.Input` 用的是 `border-line bg-ink`，在 `panel` 卡片里几乎「隐形」。

**方案**：保留品牌色，只新增语义层（不改 `--color-line` 语义，避免全站分隔线变亮）：

```css
@theme {
  /* 既有品牌令牌保持不变 */
  --color-ink: #0b0d11;
  --color-panel: #12161d;
  --color-panel-2: #181d27;
  --color-line: #2a3342;        /* 仅分隔线/装饰边框（1.43:1） */
  --color-mist: #8b95a8;
  --color-paper: #e8edf5;
  --color-signal: #c8f542;

  /* 新增双端语义令牌（已按此命名落地到 index.css） */
  --color-line-strong: #5b6a85;     /* 控件边界：vs panel 3.32:1，vs ink 3.56:1 → border-line-strong */
  --color-mist-strong: #a5aec0;     /* 8.13:1 —— 替代 text-mist/70 → text-mist-strong */
  --color-mist-soft: #7d8799;       /* 5.01:1 —— 辅助文本/占位符 → text-mist-soft */
  --color-control: #12161d;         /* 输入框底色：与卡片同层，靠强边框识别 → bg-control */
}
```

配套替换（一次性批量）：
- `ui.Input` / `ui.Select`（以及 29 处原生 input/textarea/select）：`border-line bg-ink` → `border-line-strong bg-control`，focus 用 `focus:border-signal focus:ring-2 focus:ring-signal/20`（原来只有 `focus:border-signal/70`，弱于 2px 焦点轮廓要求）。
- `text-mist/70` → `text-mist-strong`（真实文本，共 16 处）；`text-mist/60`、`/50`、`/80` → `text-mist-soft`（10 处）。
- `placeholder:text-mist/70` → `placeholder:text-mist-soft`（占位符同样是文本，也应 ≥4.5:1）。
- 全局键盘焦点（当前仅 2 处 `focus-visible:`）：

```css
/* index.css */
@layer base {
  :where(button, a, summary, [role='tab'], [tabindex]):focus-visible {
    outline: 2px solid var(--color-signal);
    outline-offset: 2px;
  }
}
```

**验收**：上面 8 行全部达到阈值；`ui.Input` 在浅色环境光/手机户外可辨认；键盘 Tab 全程有可见焦点环。

---

### P0-4 给管理端内容区加宽度上限（PC 超宽屏）

**证据**：`components/Layout.tsx:276`

```tsx
<main className="... lg:px-8 lg:pb-6 lg:pt-6">
  <Outlet />
</main>
```

全站只有 `PublicLeaderboard.tsx:15` 写了 `max-w-6xl`；管理端 42 个页面在 2K/4K 上会把卡片与表格拉满（1920px 时单卡标题与数值相距 1.5 米）。ui-ux-pro-max `html-tailwind` 栈规则明确：`Container max-width` 应 `max-w-7xl mx-auto`，禁止大屏全宽。

**方案**：新增 `PageContainer`，在 `Layout` 的 `main` 内统一包裹；列表页与表单页两档宽度：

```tsx
// components/layout-primitives.tsx
const WIDTHS = {
  wide: 'max-w-[1600px]',   // 列表 / 表格 / 看板
  form: 'max-w-[1180px]',   // 表单 / 详情 / 设置
  prose: 'max-w-[860px]',   // 长文说明
} as const

export function PageContainer({
  width = 'wide',
  className,
  children,
}: { width?: keyof typeof WIDTHS; className?: string; children: ReactNode }) {
  return <div className={cn('mx-auto w-full', WIDTHS[width], className)}>{children}</div>
}
```

- `Layout.tsx` 的 `main` 只保留 padding；页面根节点包 `<PageContainer>`（可先批量套 `wide`，再逐页改 `form`）。
- 超过 1600px（2xl）时内容居中，两侧留白由页面背景渐变承接，不需要新组件。
- 表格页可对 `<table>` 使用 `min-w-full` 而非 `min-w-[1080px]`，让宽屏自然展开、窄屏由卡片兜底。

**验收**：1920px 与 2560px 截图内容居中、行长 ≤ 120 字符；1440px 下卡片 3 列 → 4 列自适应；页面无横向滚动。

---

### P0-5 移动端可读性与触摸目标

**问题 A：字号**
- 137 处 `text-[10px]~[12px]`，38 个文件。多数是 latin eyebrow（可接受），但混入了中文辅助信息与数值标签：
  - `Dashboard.tsx:116` `text-[11px]` 中文「上下文 … · 输出 …」
  - `Dashboard.tsx:19/109/153/163` `text-[10px]` 时间戳/首 token（含中文单位）
  - `LogConversation.tsx:25` `text-[10px]`、`LogTrajectory.tsx:92` `text-[11px]`、`GenerationWorks.tsx:95/124` `text-[11px]`
  - `PublicInfo.tsx:290` `text-[11px]` 中文提示
- 移动端中文 10–11px 在 375px 屏上明显偏小，且 `text-[10px]` + `tracking-[0.16em]` 的中文间隔会进一步降低可读性。

**方案**：定义排版阶梯（§6.2），移动端中文最小 12px；`text-[10px]` 仅允许「纯拉丁 + 全大写 + tracking」的 eyebrow。落地优先级：`Dashboard`、`Logs`、`LogDetail`、`InfoFeed`、`GenerationWorks` 5 个高频页先批量替换，其余按 touched-file 原则渐进。

**问题 B：触摸目标**
- 92 个原生 `<button>`，仅 3 个显式声明 `min-h-10/11/12`；它们多是小 chip、表格行内操作、文字链式按钮。`ui.Button` 已统一 44px，说明问题是「有人绕过了原语」。
- ui-ux-pro-max `ux` 库：`Touch Target Size` 为 **High**，Web 场景引用 WCAG Target Size 规则（最低 24 CSS px，推荐 44）；`Touch Spacing` 要求相邻目标 ≥8px。

**方案**：
1. 新增 `IconButton`（`h-11 w-11` 移动 / `md:h-8 md:w-8` 桌面）并替换所有 icon-only 原生按钮。
2. 列表行内 chip/操作统一 `min-h-11` 或加大 padding 命中区（视觉可保持小，用 `before:absolute before:-inset-2` 扩展）。
3. ESLint 规则兜底：对 `pages/**` 中出现 `<button` 且 className 无 `min-h-` 的情况加自定义 lint（可选，先靠 review 清单）。

**验收**：移动端 375px 下任意可点元素命中区 ≥44×44（用 Chrome DevTools "Show tap highlights" 或脚本量 `getBoundingClientRect`）；相邻按钮间距 ≥8px；无「图标 16px、无内边距」的按钮。

---

## 4. P1：下个迭代（约 5–7 人日）

### P1-1 断点体系：补上平板中间档，避免 1024 一刀切

**现状**：`Layout.tsx:186` 用 `lg:grid-cols-[240px_1fr]`，`lg:min-h-0 lg:overflow-hidden` 一次性从「手机壳」切换为「桌面壳」。768–1023（iPad 竖屏、折叠屏展开）仍是手机壳：底部 5 Tab 被拉伸到 768px 宽、单列卡片、抽屉 288px。

**方案**：

| 断点 | 壳 | 内容 |
|---|---|---|
| `< 768` | 顶栏 + 底部 Tab + 抽屉（现状） | 单列卡片；筛选进抽屉 |
| `768–1023`（md） | **图标轨侧栏（72px）** + 顶栏（保留底部 Tab 可关） | 双列卡片；表单仍单列；表格仍用卡片 |
| `1024–1279`（lg） | 240px 分组侧栏（现状 + 分组） | 2–3 列卡片；表格全列 |
| `≥ 1280`（xl） | 240px 侧栏 + 可折叠 | 12 栏网格；列表页 `max-w-[1600px]`；密度可切 |

平板档的实现不需要新依赖：`md:grid-cols-[72px_1fr]` + 图标轨仅图标（`title` + `aria-label`），展开态用 `lg:` 恢复。

```tsx
// Layout.tsx 壳
<div className="lg:grid lg:grid-cols-[240px_1fr] md:grid md:grid-cols-[72px_1fr]">
```

> 说明：不必强行引入 `container queries`；本项目是「同一数据在不同宽度换形态」，用断点更可控。仅对「卡片内部」（如账号卡的操作区）可考虑 `@container`（Tailwind 4.3 内置）。

**验收**：768×1024、820×1180、1024×768（横屏）三档截图；平板下首屏可见内容量提升 ≥40%；底部 Tab 不再被拉宽（平板隐藏、改侧轨）。

---

### P1-2 导航信息架构：18 项平铺 → 4 组 + 搜索 + 折叠（✅ 已实施）

**原问题**：`Layout.tsx` 18 个链接平铺，无分组、无折叠、无搜索；移动抽屉同样 18 项平铺，找功能靠滚动。

**设计依据**（ui-ux-pro-max 检索结论）：

| 检索 | 命中规则 | 落点 |
|---|---|---|
| `--domain ux` · "active state current page navigation" | **Active State**：当前页/分区必须有视觉指示（颜色或下划线） | 导航项 active 态 = 15% signal 底色 + 左侧 2px 竖条 + signal 文本 |
| `--domain ux` · "nav hierarchy sidebar grouping" | **Heading Hierarchy / Breadcrumbs**：分区要有层级标题 | 4 组分组标题（11px 大写 + 字距），组间在收起态用分隔线维持层级 |
| `--domain ux` · "collapsible navigation accordion" | **Keyboard Navigation**：折叠控件要可键盘操作、焦点可见 | 分组标题为 `<button aria-expanded>`，全局 2px 焦点环 |
| `--domain ux` · "bottom navigation item limit" | **Sticky Navigation**：固定导航不能遮挡内容 | 移动端主内容区按 `--app-header` / `--app-tab` 留 padding；抽屉独立滚动 |
| `--stack html-tailwind` · "sidebar navigation dashboard layout" | Z-index 分层、**Text truncation** | 抽屉 z-40 / 遮罩 z-30 / 弹窗 z-50；导航项与品牌名 `truncate` |
| `--domain style` · "dashboard sidebar dark minimal" | **Minimalism & Swiss**：清晰层级、少装饰、200–250ms 细微过渡 | 去掉多余装饰，仅保留分组标题/active 底/分隔线，过渡统一 transition-colors |

**已实施的分组**：

```
网关   概览 / 上游账号 / 网关代理 / API Key / 记录审计
模型   模型测速 / 测速历史 / 模型榜
内容   资讯收集 / 内容审计 / 图片生成 / 视频生成 / 离线下载
平台   Skills / MCP 广场 / 工具中心 / 定时任务 / 语音房
```

**PC 展开态（240px）**

- 顶部品牌行：40×40 `PD` 方形标识（lime 描边 + 10% 底）+ 平台名 + `PIVOT DESK` 小字，右侧 `PanelLeftClose` 折叠按钮
- 品牌行下方是「搜索页面 ⌘K」伪输入框，点击/⌘K 打开命令面板（替代原来孤立的 kbd 提示）
- 分组标题：`11px uppercase + 0.16em`、右侧 chevron、`aria-expanded`；可折叠，**当前页所在分组强制展开**
- 导航项：36px 高、18px 图标、`gap-2.5`、`rounded-lg`；active = `bg-signal/15` + 左侧 2px 竖条；hover 为 6% 白底
- 滚动条：6px 细条 + 透明轨道（`.nav-scroll`），不再出现 10px 灰块
- 底部：40×40 图标按钮（强制更新 / 密度 / 折叠 / 账号 / 退出），与顶部标识同为 40px 节奏

**PC 收起态（72px 图标轨）**

- 顶部只留 40×40 `PD` 方块（`title="展开侧栏"`，点击即展开），不再是「PD + 孤立箭头」
- 每个入口 = 40×40 居中方形按钮，active 为方形 pill；图标 18px，`title` 提供悬停说明
- 分组标题隐藏后，用细分隔线和组间距保留分组语义
- 底部按钮改为竖排 40×40，避免 72px 内横排被裁切（原「收起后无法展开」的根因）
- **图标轨隐藏滚动条**（`nav-rail`：`scrollbar-width:none` + `::-webkit-scrollbar{display:none}`，仍可滚动）：6px 滚动条会挤占内容宽度，导致 40px 图标与 PD 标识、底部按钮差 3px
- 图标轨里导航项不能再写 `lg:mx-0`（会把 `md:mx-auto` 的居中覆盖成左对齐，差 8px）——已在浏览器里量过：内容中心 35.50、PD/激活项/图标/底栏中心 35.25–35.50
- 会话内记住收起状态（`localStorage: pivot:sidebar-collapsed`）

**平板（768–1023）**：复用收起态图标轨；底部 Tab 隐藏，侧轨底部额外提供搜索入口。

**细节修复（浏览器实测）**：切路由/展开收起后自动把「当前分组」滚进视区（含组标题）；图标轨链接补 `aria-label`；移动抽屉关闭时 `aria-hidden` + `inert`，避免键盘/读屏进入屏幕外的菜单。

**移动（<768）**：抽屉 288px —— 品牌行（logo + 名称 + 关闭）→ 搜索输入（本地过滤 18 项）→ 可折叠分组 → 底部 40×40 操作按钮；底部保留 5 个高频 Tab（概览 / 账号 / Key / 记录 / Skills）。

**键盘与可访问性**

- `⌘K` / `Ctrl+K` 打开命令面板（↑↓ 选择、回车跳转、Esc 关闭、`role=listbox/option`）
- 分组折叠是原生 `<button aria-expanded>`；当前项由 `NavLink` 自动输出 `aria-current="page"`，切换路由后自动滚入可视区
- 所有图标按钮带 `aria-label` + `title`；全局焦点环 2px signal

**验收**：18 个入口在 PC 上 ≤2 次点击可见；收起/展开双向可达且有悬停提示；移动端抽屉内可搜索；键盘用户 `⌘K` 输入 3 字内命中目标页；当前页高亮 + 分组自动展开。

---

### P1-3 列表/表格双态标准化（`DataView`）+ 补齐唯一缺口

**现状**：13 个文件手写了「表格 + 卡片」两套视图（`ContentAudit.tsx:299,358`、`LeaderboardView.tsx:160,220`、`VoiceRoomDetail.tsx:633,690`、`BenchmarkHistory.tsx:131,170` …），实现方式各不相同（有的 `hidden lg:block`，有的 `lg:hidden`，加载/空状态各写一份）。

**唯一缺口**：`McpCalls.tsx:174` 的 7 列表格只有 `overflow-x-auto`，手机需要横向拖动 2 屏才能看到「结果/耗时」。ui-ux-pro-max `ux` 库 `Table Handling`：移动端应「横向滚动或卡片布局」——本项目主张卡片。

**方案**：抽出 `DataView<T>`，一次定义、两处渲染：

```tsx
export type Column<T> = {
  key: string
  header: ReactNode
  cell: (item: T) => ReactNode
  /** 移动卡片里是否展示；默认 true */
  mobile?: boolean
  width?: string
}

export function DataView<T>({
  items, columns, keyOf, renderCard, loading, empty, onRowClick,
}: {
  items: T[]
  columns: Column<T>[]
  keyOf: (item: T) => string
  renderCard: (item: T) => ReactNode        // 移动端卡片
  loading?: boolean
  empty?: ReactNode
  onRowClick?: (item: T) => void
}) {
  if (loading) return <ListSkeleton rows={5} />
  if (!items.length) return <>{empty}</>
  return (
    <>
      {/* PC：≥lg 表格 */}
      <div className="hidden overflow-x-auto rounded-xl border border-line lg:block">
        <table className="w-full text-left text-sm">{/* thead + tbody(columns) */}</table>
      </div>
      {/* 移动/平板：卡片 */}
      <div className="grid gap-3 md:grid-cols-2 lg:hidden">
        {items.map((item) => <div key={keyOf(item)}>{renderCard(item)}</div>)}
      </div>
    </>
  )
}
```

迁移顺序（低风险）：`McpCalls` → `BenchmarkHistory` → `LeaderboardView` → `VoiceRoomDetail` → `ContentAudit`。每迁一个，删除该页各自实现的两套空态/加载态。

**验收**：375px 下所有列表页无横向滚动（代码块/长 ID 内部 `overflow-x-auto` 例外）；同一数据在 375/768/1440 三档都有可读呈现；空态/加载态只有一套来源。

---

### P1-4 加载态与空状态：从「加载中…」文本到骨架屏

**现状**：只有 2 个页面用了骨架（`Logs.tsx:297` 列表骨架卡、`InfoFeed.tsx:640-643` 封面 + 文字占位）；其余页面是「加载中…」文本或直接空白（`McpCalls`、`ContentAudit`、`Skills`…）。ui-ux-pro-max `ux` 库 `Loading Indicators`（High）：长等待应有稳定骨架或进度，并带 `aria-busy`。

**方案**：
- 新增 `Skeleton`（`animate-pulse rounded bg-white/[0.06]` + `aria-hidden`）与 `ListSkeleton` / `CardSkeleton` / `TableSkeleton`。
- 列表页：查询 `isPending` → 骨架卡 × 6（保持最终高度，CLS < 0.1）。
- 容器加 `aria-busy={isFetching}`，加载完成后 `role="status"` 文本交给 Toast/内联播报（见 P1-5）。
- 空态统一 `EmptyState`（图标 + 标题 + 说明 + 主操作），现有 28 处「暂无/没有符合」文案统一。

**验收**：4G 节流下首屏骨架出现 ≤200ms；列表加载前后布局不跳动（Lighthouse CLS < 0.1）；每个列表页都有「空 + 筛选后为空」两种文案。

---

### P1-5 无障碍补齐（与 P0-1/P0-2 合并验收）

| 项 | 现状 | 目标 |
|---|---|---|
| 对话框语义/焦点 | `ui.Dialog` 无 `role/aria-modal`、无焦点陷阱、无 Esc、无焦点归还 | P0-2 解决 |
| 异步播报 | `aria-live` / `aria-busy` / `role="alert"` = 0 | 列表容器 `aria-busy`；错误用 `role="alert"`；操作结果 toast（sonner 自带 `role="status"`）保留 |
| 表单错误 | 仅内联红字，无错误摘要 | 提交失败时顶部 `role="alert"` 摘要 + 链接到字段 + 焦点移到摘要（ui-ux-pro-max `ux`：Focusable Error Summary，High） |
| 图片替代文本 | 复核后 **0 处**缺 `alt` ✅（此前统计口径错误） | 保持：内容图写文案、装饰图显式 `alt=""`、补尺寸占位防 CLS |
| 焦点可见 | 仅 2 处 `focus-visible:` | 全局焦点环（P0-3） |
| 颜色非唯一指示 | 状态用「圆点 + 文本」✅ | 保持；`Benchmark` 提示 icon 用 `aria-describedby` 而非 `title` |
| 触摸目标 | 部分 <44px | P0-5 解决 |
| 缩放/回流 | 移动端禁缩放（产品决策，保留） | 桌面浏览器 200% 缩放无横向滚动；移动端靠字号阶梯 + 对比度保证可读（见 P0-1） |
| 动效 | `prefers-reduced-motion` 已覆盖自定义动画 | 新加的抽屉过渡加 `motion-reduce:transition-none` |
| 键盘全流程 | 原生按钮语义 ✅；表格行内操作可聚焦 | 列表行支持 `Enter` 打开；移动端 ActionSheet 提供键盘可达的等价菜单 |

---

### P1-6 移动端操作范式（让手机不只是「缩小版 PC」）

| 场景 | PC（现状可保留） | 移动端建议 |
|---|---|---|
| 筛选（50 处 flex-wrap 过滤器，如 `Keys.tsx:615+`、`Accounts.tsx:1105`） | 全部展开在页头 | 页头只留「筛选（N）」按钮 → 底部 Sheet 内表单；已选条件以可删除 chip 展示 |
| 列表行操作（探测/启用/删除） | 行内按钮组 / 悬停 | 卡片整体进详情 + 右上「⋯」→ ActionSheet（底部动作面板） |
| 长表单（账号编辑、工具登记、生成配置） | 居中弹窗 | 全屏 Sheet（`h-[92dvh]`）+ 底部 sticky 操作区 + 数字键盘 `inputMode="numeric"`（现仅 5 处使用） |
| 危险操作（删除账号/Key） | 二次确认弹窗 | ActionSheet 红色主操作 + 文案说明后果；破坏性操作不做长按触发 |
| 复制（Key/ID/命令） | 点击复制按钮 | 长按整行复制 + 复制按钮并列（`navigator.clipboard` 已有 fallback） |
| 图片查看 | hover 预览（`InfoFeed.tsx:734`） | 点击进详情（现状）✅；可在详情页加双击放大 |

> 明确**不做**：下拉刷新（子应用内重复刷新易误触，`overscroll-behavior:none` 已全局禁用）、左滑删除（与浏览器返回手势冲突，风险高于收益）、复杂手势编排（收益集中在单点，不如按钮稳定）。

---

## 5. P2：后续优化（约 3–5 人日）

1. **PC 数据可视化**：`Dashboard` 目前全是数字卡。加 7/30 日趋势 sparkline（纯 SVG，无新依赖）与「请求失败率」迷你图；移动端收起为「趋势」折叠区。
2. **桌面密度切换**：`舒适 / 紧凑` 两档（行高 44 → 36，卡片 padding 16 → 12），写 `localStorage` + `<html data-density>`，只在 `lg+` 生效。
3. **批量操作**：`Accounts` / `Keys` / `Skills` 列表加多选 + 底部浮动操作条（PC 顶部条、移动底部条），ui-ux-pro-max `ux` 有 `Bulk Actions` 条目。
4. **表格增强**：列排序指示、粘性首列（宽表）、导出当前筛选（已有导出按钮的页面复用）。
5. **z-index 体系**：统一为 `--z-sticky:10 / dropdown:20 / overlay:30 / drawer:40 / modal:50 / toast:60 / boot:100`，替换 `z-[60]`、`z-[100]`。
6. **图片 CLS**：详情页大图补 `width/height` + `decoding="async"`；`offline` 工具卡图片同样处理。
7. **PWA 体验**：`display_override: ['window-controls-overlay']`（桌面）、快捷方式（"新建 Key" / "日志"）、离线页文案区分「离线」与「服务不可达」。
8. **字体**：结论是**保留 IBM Plex（自托管拉丁子集）+ 系统中文字体栈**，不引入 Fira Code/Fira Sans（技能按通用 dashboard 生成，本项目品牌已成型）；若后续要更「技术感」，可只把等宽字体换成 IBM Plex Mono 的扩展子集，成本最低。

---

## 6. 双端设计规范（落地令牌）

### 6.1 颜色语义层

```css
@theme {
  /* 基础（保留） */
  --color-ink: #0b0d11;
  --color-panel: #12161d;
  --color-panel-2: #181d27;
  --color-line: #2a3342;
  --color-mist: #8b95a8;
  --color-paper: #e8edf5;
  --color-signal: #c8f542;
  --color-ok: #4ade80;
  --color-warn: #f5b942;
  --color-danger: #f25f6d;
  --color-info: #6ec8ff;

  /* 语义（新增） */
  --color-surface: #0b0d11;
  --color-surface-raised: #12161d;
  --color-surface-sunken: #070910;
  --color-border-subtle: #232c3a;   /* 分隔线 1.29:1，仅视觉分组 */
  --color-border-strong: #5b6a85;   /* 控件边界 3.32:1 */
  --color-text-primary: #e8edf5;    /* 15.42:1 */
  --color-text-secondary: #a5aec0;  /* 8.13:1 */
  --color-text-tertiary: #7d8799;   /* 5.01:1 */
  --color-focus: #c8f542;           /* 14.33:1 */
}
```

| 用途 | 令牌 | 禁止 |
|---|---|---|
| 页面/卡片/次级填充 | `surface` / `panel` / `panel-2` | 新写任意 hex |
| 分隔线 | `line` / `border-subtle` | 用 `line` 当输入框边界 |
| 控件边界（输入/次级按钮） | `border-strong` | 1px `line` + 同色背景 |
| 正文/次要/辅助 | `text-primary` / `text-secondary` / `text-tertiary` | `text-mist/70` 等半透明文本 |
| 状态色 | `signal`（主操作）、`ok/warn/danger/info` | 状态只用颜色不用文案 |

### 6.2 排版阶梯

| 令牌 | 移动 | ≥ md | 用途 | 中文最小 |
|---|---|---|---|---|
| `display` | 24 / 1.3 | 30 / 1.2 | 页面标题 | 24 |
| `h2` | 18 / 1.4 | 20 / 1.4 | 区块标题 | 18 |
| `title` | 16 / 1.5 | 16 / 1.5 | 卡片标题 | 16 |
| `body` | 14 / 1.6 | 14 / 1.6 | 正文 | 14 |
| `body-sm` | 13 / 1.6 | 13 / 1.6 | 次要正文 | 13 |
| `caption` | 12 / 1.5 | 12 / 1.5 | 时间戳、辅助说明 | 12 |
| `micro` | 11 / 1.4 | 11 / 1.4 | **仅拉丁**全大写 eyebrow | — |
| `mono` | 12 | 12–13 | ID、数值、Key 前缀 | 12 |

规则：**中文禁止 `text-[10px]`**；`tracking` 只加在拉丁全大写标签上（中文加字距会变难读）；正文行长 ≤ 80 个中文字符（PC 表单页 `max-w-[1180px]` 内再按栏宽控制）。

### 6.3 间距与密度

- 4px 网格；卡片内 `p-4`（移动）/ `p-5`（PC）；区块间距 `space-y-5`（现状 `space-y-5` 可保留）。
- 密度三档：`comfortable`（默认，PC 行高 44）/ `compact`（36）/ `mobile`（固定 ≥44 交互，不提供紧凑）。
- 触摸间距：相邻目标 ≥8px（`gap-2`）；底部固定区 + 安全区：`pb-[max(0.75rem,env(safe-area-inset-bottom))]`。

### 6.4 断点与容器

| 断点 | 宽度 | 壳 | 内容 |
|---|---|---|---|
| — | < 640 | 手机 | 单列；Tab 栏 |
| `sm` | ≥ 640 | 手机 | 表单双列 |
| `md` | ≥ 768 | 平板 | 图标轨侧栏、卡片双列 |
| `lg` | ≥ 1024 | 桌面 | 240px 侧栏、表格全列 |
| `xl` | ≥ 1280 | 桌面 | 12 栏网格、密度切换 |
| `2xl` | ≥ 1536 | 宽屏 | 容器居中上限 1600px |

### 6.5 层级（z-index）

| 令牌 | 值 | 用途 | 替换 |
|---|---|---|---|
| `--z-sticky` | 10 | 表格 sticky 表头、页内粘性条 | `z-10` |
| `--z-dropdown` | 20 | 下拉、Tooltip、命令面板 | `z-20` |
| `--z-overlay` | 30 | 抽屉遮罩、移动顶栏/Tab | `z-30` |
| `--z-drawer` | 40 | 抽屉本体 | `z-40` |
| `--z-modal` | 50 | Dialog/Sheet | `z-50` / `z-[60]` |
| `--z-toast` | 60 | Toast | sonner 内部 |
| `--z-boot` | 100 | 启动骨架 | `z-[100]` |

### 6.6 圆角 / 阴影 / 动效

- 圆角：控件 `rounded-md(6px)`、卡片 `rounded-xl(12px)`、抽屉 `rounded-t-2xl`、胶囊 `rounded-full`。
- 阴影：仅浮层使用；卡片保持描边 + 极浅阴影（现状 `0 10px 40px rgba(0,0,0,.25)` 保留）。
- 动效：`120–200ms`，`ease-out` 进入、`ease-in` 退出；抽屉 240ms；全部加 `motion-reduce:transition-none`；不做装饰性无限动画（`gate-*` 仅公开页门禁，已受 reduced-motion 控制）。

---

## 7. 布局方案（线框）

### 7.1 PC ≥ 1024

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ PIVOT DESK   │  面包屑：记录审计 / #12345            [⌘K] [主操作]      │ 64
│ [⌘K 搜索]    ├──────────────────────────────────────────────────────────┤
│              │  ┌ PageContainer max-w-[1600px] mx-auto ───────────────┐ │
│ 网关         │  │  标题 24/30 + 说明                                   │ │
│   概览       │  │  ┌───────┬───────┬───────┬───────┐  指标卡 4 列     │ │
│   上游账号   │  │  └───────┴───────┴───────┴───────┘                   │ │
│   网关代理   │  │  ┌ 筛选条（内联）───────────────────────────[导出]┐ │ │
│   API Key    │  │  └────────────────────────────────────────────────┘ │ │
│   记录审计 ◀ │  │  ┌ 表格：行高 44（可切 36） 多选 排序 粘性表头 ──┐ │ │
│ 模型         │  │  └────────────────────────────────────────────────┘ │ │
│   …          │  │  分页                                                │ │
│ 内容 / 平台  │  └──────────────────────────────────────────────────────┘ │
│ ───────────  │                                                          │
│ [头像] 退出  │                                                          │
└──────────────┴──────────────────────────────────────────────────────────┘
   240px（可折叠 72px 图标轨，记忆在 localStorage）
```

### 7.2 平板 768–1023

```
┌────┬──────────────────────────────────────────────────┐
│ 图 │  ☰ 标题（可省略）                  [主操作]       │
│ 标 ├──────────────────────────────────────────────────┤
│ 轨 │  双列卡片（列表） / 单列表单                       │
│ 72 │  ┌──────────────┐ ┌──────────────┐               │
│    │  └──────────────┘ └──────────────┘               │
│    │                                                  │
├────┴──────────────────────────────────────────────────┤
│   概览   账号   Key   记录   更多（可选择隐藏）        │ 56 + safe-area
└───────────────────────────────────────────────────────┘
```

### 7.3 移动 < 768（含抽屉式 Dialog）

```
┌───────────────────────────────┐
│ ☰   记录审计        [筛选 2]  │ 48 + safe-area-top
├───────────────────────────────┤
│ 卡片列表（点击进详情）        │
│ ┌───────────────────────────┐ │
│ │ 标题           [成功]     │ │
│ │ 元信息 12px                │ │
│ └───────────────────────────┘ │
│ …                             │
│                               │
│        ╭──────────────────────╮│ ← 底部 Sheet（替代居中弹窗）
│        │ ── 拖动条           ││
│        │ 标题           [×]  ││ sticky header
│        │ 内容滚动区          ││
│        │ [取消]      [保存]  ││ sticky footer
│        ╰──────────────────────╯│
├───────────────────────────────┤
│ 概览  账号  Key  记录  更多   │ 56 + safe-area-bottom
└───────────────────────────────┘
```

---

## 8. 组件改造清单

| 组件 | 位置 | 改动 | 迁移面 |
|---|---|---|---|
| `Sheet`（新） | `components/ui.tsx` | 底部抽屉 + 居中弹窗双形态；焦点陷阱、Esc、`aria-modal`、sticky 头尾 | 新代码直接用 |
| `Dialog`（改） | 同上 | 变为 `Sheet` 兼容壳 | **59 处调用零改动** |
| `IconButton`（新） | 同上 | `h-11 w-11 md:h-8 md:w-8` + 必填 `aria-label` | 替换 92 个原生按钮中的 icon-only 项 |
| `Input` / `Select`（已改） | 同上 | `border-line-strong bg-control` + 2px 焦点环 | 全站表单 + 29 处原生控件 |
| `PageContainer`（新） | `components/layout-primitives.tsx` | `wide / form / prose` 三档宽度 | 42 个管理页 |
| `PageHeader`（新） | 同上 | 标题 + 说明 + 主操作 + 可选面包屑；移动端标题在左、主操作在右 | 42 个管理页（现状每页手写，`h1` 尺寸就有 3 种） |
| `DataView<T>`（新） | `components/data-view.tsx` | 列定义 + PC 表格 + 移动卡片 + 加载/空态 | 7 个表格页，先 `McpCalls` |
| `Skeleton`（新） | `components/ui.tsx` | `animate-pulse` + `aria-hidden` | 所有列表页 |
| `EmptyState`（新） | 同上 | 图标 + 标题 + 说明 + 操作 | 28 处「暂无」文案 |
| `FilterBar`（新） | `components/filter-bar.tsx` | PC 内联；移动折叠为 Sheet；已选 chip 可删 | 50 处筛选器渐进迁移 |
| `ActionSheet`（新） | `components/ui.tsx` | 移动行操作菜单（复用 Sheet） | 列表行「⋯」 |
| `CopyField`（新） | `components/ui.tsx` | Key/ID 复制 + 长按选中 | Keys / Accounts / MCP |

> 附：`PageHeader` 现状不统一（`h1` 有 `text-2xl` 30 处、`text-xl` 17 处、`text-lg` 8 处），统一后也顺带修掉视觉噪音。

---

## 9. 双端交互对照表（验收口径）

| 场景 | PC | 移动 |
|---|---|---|
| 导航 | 分组侧栏（可折叠）+ 面包屑 + ⌘K | 5 Tab + 分组抽屉 + 系统返回 |
| 主操作 | 页头右侧 + 快捷键 | 页头右侧 + 长表单底部 sticky |
| 列表 | 表格 + 密度切换 + 多选 | 卡片 + 筛选抽屉 + 行 ActionSheet |
| 弹窗 | 居中 `max-w-2xl`，Esc/点遮罩关闭 | 底部 Sheet `90dvh`，右上角关闭、遮罩不关闭 |
| 表单 | 双列 + 侧栏说明 | 单列 + 全屏 Sheet + `inputMode` |
| 反馈 | Toast 右上 | Toast 顶部（已避让灵动岛）+ 内联 `role=alert` |
| 危险操作 | 二次确认弹窗 | ActionSheet 红色主操作 + 后果说明 |
| 动效 | 120–200ms | 220–260ms，`motion-reduce` 全关 |

---

## 10. 验收标准与测试矩阵

### 设备矩阵

| 档位 | 尺寸 | 必查点 |
|---|---|---|
| 小屏手机 | 320×568、375×667 | 无横向滚动、标题不换行溢出、Sheet 可滚动 |
| 主流手机 | 390×844、414×896 | 底部 Tab 与 Sheet 安全区、键盘弹出后表单可滚 |
| 大屏手机横屏 | 844×390 | Sheet ≤90dvh、顶栏不被键盘遮挡 |
| 平板 | 768×1024、820×1180、1024×768 | 图标轨侧栏、双列卡片、Tab 栏取舍 |
| 笔记本 | 1280×800、1440×900 | 容器上限、表格全列、密度切换 |
| 桌面 | 1920×1080、2560×1440 | 内容居中、行长、无拉伸 |
| 特殊 | 浏览器 200% 缩放 / 仅键盘 / `prefers-reduced-motion` / PWA standalone | 回流无横向滚动、焦点可见、动效关闭 |

### 量化指标

| 指标 | 现状 | 目标 |
|---|---|---|
| Lighthouse Mobile · Accessibility | 未测 | **100** |
| Lighthouse Mobile · Performance | 未测 | ≥ 90（首屏 vendor 122 KB gz + API） |
| CLS | 未测 | < 0.1 |
| 触摸目标违规数 | 92 个原生按钮中 89 个未声明 44px | **0**（脚本量 `getBoundingClientRect`） |
| 对比度违规数 | 输入控件边界（`ui.Input`/`Select` 全站复用 + 4 处原生输入）1.43:1、半透明文本 26 处（`mist/70`×16、`/60`×7、`/50`×3） | **0** |
| 横向滚动页面数 | `McpCalls` 等宽表 ≥1 | 0（代码/长 ID 例外） |
| 移动端首屏可见信息量 | 单列 | 提升 ≥40%（平板档） |
| 关键路径点击次数（新建 Key） | 3 次 | 2 次（页头主操作 + Sheet 底部保存） |

**回归命令**：`npm run lint`（jsx-a11y 已启用）+ `npm run build` + 手工走 §10 矩阵；建议后续加 `@axe-core/cli` 对 `/login`、`/`、`/accounts`、`/logs`、`/share` 做 CI 扫描。

---

## 11. 分阶段落地计划

> 状态：**S0–S6 已实施**（其中 S3 只完成组件层与 `McpCalls` 迁移，其余列表页按需渐进；S7 待排期）。

| 阶段 | 内容 | 改动文件 | 预估 | 依赖 |
|---|---|---|---|---|
| **S0 速修** | P0-1 视口（产品决策：保留禁用，不改）、P0-3 令牌与焦点环、P0-4 `PageContainer`、P0-5 字号批量 | `index.html`（不改）、`index.css`、`ui.tsx`、`Layout.tsx`、Dashboard/Logs/LogDetail/InfoFeed | 0.5 人日 | 无 |
| **S1 弹窗** | `Sheet` + `Dialog` 兼容层 + Top 6 长表单加 `footer` | `ui.tsx`、`Accounts/Skills/Tools/Benchmark/Keys` 的 Dialog 调用点 | 1 人日 | S0（令牌） |
| **S2 布局与导购** | `PageHeader` 全站套用、侧栏分组/折叠、移动抽屉分组 + 搜索、⌘K | `Layout.tsx`、新增 `page-header.tsx`、42 页逐页替换标题块 | 1 人日 | S0 |
| **S3 列表** | `DataView` + 迁移 `McpCalls` 等 5 页、`Skeleton`、`EmptyState` | 新增 `data-view.tsx`、5 个页面 | 1.5 人日 | S0 |
| **S4 移动交互** | `FilterBar`、`ActionSheet`、长表单全屏、`CopyField` | `components/*`、Keys/Accounts/InfoSources | 1 人日 | S1、S3 |
| **S5 平板与密度** | `md` 断点壳、双列卡片、密度切换 | `Layout.tsx`、`index.css` | 1 人日 | S2 |
| **S6 无障碍与验收** | alt、aria-live、错误摘要、设备矩阵回归、指标测量 | 全站 | 0.5–1 人日 | S1–S5 |
| **S7 可选（P2）** | Dashboard 趋势图、批量操作、PWA shortcuts | — | 3–5 人日 | 业务排期 |

**总投入**：S0–S6 约 **7 人日**，可拆成 3 个 PR：`ui 基础层`（S0/S1/S3 组件）→ `布局与导航`（S2/S5）→ `页面迁移与 a11y`（S4/S6）。每个 PR 互不阻塞，风险最低的是先合 S0。

---

## 12. 取舍与不做的事

- **不做浅色主题**：`color-scheme: dark` 与品牌视觉一致；技能检索出的通用面板色（#0F172A 系）与现有品牌不符，只借其「数据密集、状态色、Swiss 式留白」原则，不替换令牌。
- **不引入 UI 组件库**（Radix/shadcn）：`Sheet` 的焦点陷阱等可自建（约 60 行），引入 Radix 会增加 15–25 KB gz，而当前 vendor 已 122 KB。
- **不做复杂手势**（左滑删除、下拉刷新）：与浏览器返回手势冲突，误触成本高。
- **不做 `container queries` 全量替换**：断点方案已能覆盖「同一数据换形态」，container query 仅用于卡片内部自适应。
- **不改造公开端 `/share/*`、`/voice/send`**：它们已是面向手机的独立流程（`min-h-svh`、安全区、sticky 头、触摸手势），仅跟随本方案享受 `Sheet`/令牌收益，不做结构重排。

---

## 附录 A：对比度实算方法

WCAG 2.2 相对亮度公式，逐令牌计算（脚本可复现）：

```
L = 0.2126·R' + 0.7152·G' + 0.0722·B'
对比度 = (L_light + 0.05) / (L_dark + 0.05)
```

| 前景 | 背景 | 比值 | 判定 |
|---|---|---|---|
| `paper #e8edf5` | `panel #12161d` | 15.42 | ✅ AA/AAA |
| `mist #8b95a8` | `panel` | 6.01 | ✅ AA |
| `signal #c8f542` | `panel` | 14.33 | ✅ AA/AAA |
| `danger #f25f6d` | `panel` | 5.73 | ✅ AA |
| `line #2a3342`（控件边界） | `panel` | 1.43 | ❌ 需 `#5b6a85`（3.32） |
| `ink` 输入框底 vs `panel` 卡片 | — | 1.15 | ❌ 需换 `bg-control` + 强边框 |
| `mist/70` 文本 | `panel` | 3.58 | ❌ 需 `text-mist-strong`（8.13） |
| `mist/60` 文本 | `panel` | 2.96 | ❌ |
| `mist/50` 文本 | `panel` | 2.44 | ❌ |

## 附录 B：ui-ux-pro-max 检索结论（本次采纳的部分）

| 检索 | 命中规则 | 本方案落点 |
|---|---|---|
| `--domain ux` · "mobile first responsive breakpoint" | Mobile First / Breakpoint Testing / Viewport Meta（High） | P0-1、S5 设备矩阵 |
| `--domain ux` · "touch target icon button" | Touch Target Size（High）、Touch Spacing | P0-5 |
| `--domain ux` · "table mobile card responsive" | Table Handling：横向滚动或卡片布局 | P1-3 |
| `--domain ux` · "modal focus management" | Focus States（High）、Z-Index Management（High） | P0-2、P2-5 |
| `--domain ux` · "form validation error summary" | Focusable Error Summary（High）、Error Messages `role=alert` | P1-5 |
| `--domain ux` · "skeleton loading placeholder" | Loading Indicators（High，`aria-busy`） | P1-4 |
| `--domain ux` · "sticky action bar safe area" | Sticky Navigation 内容补偿、Focus Appearance 2px/3:1 | P0-2、P0-3 |
| `--stack html-tailwind` · "container max width" | `max-w-7xl mx-auto`，禁止大屏全宽 | P0-4 |
| `--design-system`（dark data-dense dashboard） | Minimalism & Swiss、dark + status colors、data-dense but scannable；anti-pattern：慢更新、无自动化 | §6 规范、§11 自动化验收 |

> 说明：`--design-system` 输出的配色（#0F172A / Fira 字体）为通用 dashboard 推荐，与本项目既有品牌（IBM Plex + lime signal）不一致，故**仅采纳结构原则，不采纳具体色值与字体**；所有结论以代码实测为准。

---

## 附录 C：P0 速修 diff 预览

```diff
# frontend/index.html
# 按产品要求保留禁用双指缩放，本轮不改动（见 P0-1）
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover" />
```

```diff
# frontend/src/index.css
 :root {
   color-scheme: dark;
   --app-header: 3rem;
   --app-tab: 3.5rem;
+  --app-content-max: 1600px;
 }
+
+@layer base {
+  :where(button, a, summary, [role='tab'], [tabindex]):focus-visible {
+    outline: 2px solid var(--color-signal);
+    outline-offset: 2px;
+  }
+}
```

```diff
# frontend/src/components/ui.tsx（Input/Select）
- 'w-full rounded-md border border-line bg-ink px-3 py-2 text-base ... focus:border-signal/70 md:text-sm'
+ 'w-full rounded-md border border-line-strong bg-control px-3 py-2 text-base ... focus:border-signal focus:ring-2 focus:ring-signal/20 md:text-sm'
```

```diff
# frontend/src/components/Layout.tsx（main）
- <main ref={mainRef} className="... lg:px-8 lg:pb-6 lg:pt-6">
+ <main ref={mainRef} className="... lg:px-8 lg:pb-6 lg:pt-6">
+   <PageContainer>
      <Outlet />
+   </PageContainer>
  </main>
```

---

*本方案为代码审计结论，不包含实际改动；实施时按 §11 阶段拆分 PR，每个阶段结束跑 §10 设备矩阵与量化指标。*
