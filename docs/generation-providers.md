# 图片 / 视频生成：供应商接入说明

面向要**新增一个生成供应商**的开发者。目标是：接入一家新供应商只需要新增一个文件，
不需要改编排层、路由或前端。

## 分层

```
app/generation/
├── base.py          ProviderAdapter 协议 + GenerationUpdate（编排层唯一认识的类型）
├── catalog.py       公共词汇表：ModelSpec / ParamSpec / 共享比例与尺寸常量
├── providers/       一家供应商一个文件（自动发现，见下）
│   ├── gsai.py
│   └── rightapi.py
├── registry.py      自动发现 providers/、聚合目录、按 (provider, model) 查找
└── service.py       任务编排：提交 / 轮询 / 转存结果（不认识任何具体上游）
```

关键点：**上游差异全部收敛在适配器内部**。适配器把上游协议翻译成 `GenerationUpdate`，
编排层与前端都只认 `GenerationUpdate` 和 `ModelSpec`。

## 新增供应商：只加一个文件

在 `app/generation/providers/` 下新建 `acme.py`，暴露以下模块级名字，`registry` 会自动发现：

```python
from app.generation.base import GenerationUpdate, InputMedia, ProviderError
from app.generation.catalog import ModelSpec, ParamSpec, IMAGE_RATIOS, IMAGE_SIZE_PARAM, opts

PROVIDER = "acme"                     # DB 里 generation_providers.provider
LABEL = "Acme（acme）"                 # 前端展示名
DEFAULT_BASE_URL = "https://api.acme.com"   # 管理员可在配置界面改
KINDS = ("image",)                    # 支持的任务类型：image / video
ORDER = 100                           # 可选，越小越靠前；决定「默认供应商」

class AcmeAdapter:
    provider = PROVIDER

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def submit(self, *, kind, model, prompt, params, images: list[InputMedia], audios: list[InputMedia]) -> GenerationUpdate:
        """提交任务。异步型上游在这里返回 upstream_id；同步型可直接返回 succeeded + results。"""
        ...

    def poll(self, upstream_id: str) -> GenerationUpdate:
        """查询任务。返回 status 与 results（结果 URL 列表）。"""
        ...

ADAPTER = AcmeAdapter

MODELS = (
    ModelSpec(
        id="acme-image-1",
        label="acme-image-1",
        kind="image",
        params=(ParamSpec("aspectRatio", "画面比例", "select", opts(IMAGE_RATIOS), "1:1"), IMAGE_SIZE_PARAM),
        supports_images=True,
        max_images=9,
    ),
)
```

`MODELS` 是声明式目录，前端表单由它自动渲染：加了什么参数，界面就出现什么字段，**不用改前端**。

### 约定

| 名字 | 必填 | 说明 |
|------|------|------|
| `PROVIDER` | 是 | 供应商标识，落库到 `generation_tasks.provider` |
| `LABEL` | 否 | 缺省用 `PROVIDER` |
| `DEFAULT_BASE_URL` | 否 | 配置界面的默认地址 |
| `KINDS` | 否 | 缺省由 `MODELS` 的 kind 推导 |
| `ADAPTER` | 是 | 实现 `submit` / `poll` 的类 |
| `MODELS` | 是 | 该供应商的模型清单 |
| `ORDER` | 否 | 默认 100；`gsai` 是 0，因此默认供应商不会漂移 |

把 `KINDS` 声明正确很重要：RightAPI 只有图片，前端就不会给它显示视频模型，
后端也会在 `create_task` 阶段直接拒绝视频任务。

### `GenerationUpdate` 与状态

统一状态：`running` / `succeeded` / `failed` / `violation`，与 `generation_tasks.status` 对齐。
判定终态用 `update.terminal`。`progress` 取 0–100。

`submit()` 的两种形态：

- **异步上游**（大多数）：返回 `GenerationUpdate(status="running", upstream_id=...)`，之后由 `poll()` 收敛；
  提交阶段就被上游拒绝时，返回 `status="failed"` 并填 `error`。
- **同步上游**：直接返回 `status="succeeded"` + `results`。

`poll()` 只在拿不到任务号或 HTTP 层出错时抛 `ProviderError`；业务失败用 `status="failed"`。

## 模型目录为什么挂在供应商下面

不同供应商的模型集合不同，同名模型也会同时出现在多家（都卖 `nano-banana-2`）。
如果目录是一张全局扁平表，同名模型会互相覆盖，前端也无法知道某个模型属于谁。因此：

- 目录条目带 `provider` / `provider_label`；
- 查找一律是 `(provider, model)` 二维的（`service.resolve_spec`）；
- 建任务时同时校验「供应商支持该类型」与「模型确实属于该供应商」；
- 前端下拉框的 value 用 `provider:model` 组合键（`lib/generation.ts` 的 `modelKey`）。

## 默认模型

`generation_settings.default_image_model` / `default_video_model` 统一存成 `provider:model`。
读取时 `_qualified_default` 会把旧配置里的**裸模型名**反查回供应商，所以升级不用改数据。

## 自定义模型（供应商没有公开模型列表时）

有些供应商（如 RightAPI）只在自家后台展示可用模型名。配置界面的「自定义模型」字段可以把
模型名直接填进去（逗号或空格分隔），会与静态 `MODELS` 合并进目录，用一套通用的
比例 / 分辨率参数兜底 —— 不必为了试一个模型先改代码。

同名时静态清单优先（`_extra_specs` 会跳过已在 `MODELS` 里的名字）。

供应商同时支持图片和视频时（如 gsai），用 `模型名:video` / `模型名:image` 指定类型，
不写就按默认类型处理；后缀不是该供应商支持的类型时不会被采纳，也不会把模型名悄悄改写
（`extra_model_specs` 的行为由测试固定）。

## 内置供应商

### gsai（Grsai）

- 提交 `POST {base}/v1/api/generate`（`replyType=async`），查询 `GET {base}/v1/api/result?id=`
- 支持图片 + 视频

### rightapi（RightAPI / rc_draw）

文档：<https://docs.rightapi.ai/docs/rc_draw/>

- 提交 `POST {base}/draw/v1/images/generations`，请求体固定带 `"async": true`
- 查询 `GET {site}/v1/tasks/{task_id}` —— **站点级，不带 `/draw`**
- `base_url` 填 `https://www.rightapi.ai` 或文档里的 `https://www.rightapi.ai/draw` 都可以，
  适配器用 `_draw_base` / `_site_base` 推导两个前缀
- 只有图片；`mask`（局部重绘）与 `variations` 不支持，结果固定为图片 URL
- 模型清单照 RightAPI 后台「模型列表 → **画图 1x**」端点核对过（OpenAI 5 + Google 4 = 9 个），
  各型号的 `imageSize` 档位取自该页的「模型说明」：

  | 型号 | 尺寸参数键 | `imageSize` |
  |------|-----------|-------------|
  | `nano-banana`（gemini-2.5-flash-image） | `aspectRatio`（比例） | 1K |
  | `nano-banana-2`（gemini-3.1-flash-image-preview） | `aspectRatio`（比例） | 1K / 2K / 4K |
  | `nano-banana-2-lite`（gemini-3.1-flash-lite-image） | `aspectRatio`（比例） | 1K |
  | `nano-banana-pro`（gemini-3-pro-image-preview） | `aspectRatio`（比例） | 1K / 2K / 4K |
  | `gpt-image-2` | `size`（像素串） | 1K |
  | `gpt-image-2-vip` | `size`（像素串） | 1K / 2K / 4K |
  | `gpt-image-2.5` / `-flare` / `-sunburst` | `size`（像素串） | 不传该参数 |

  > **比例类模型用 `aspectRatio`，不用 `size`。** 文档说两者「作用同 size 的比例写法」，
  > 而 `aspectRatio` 是本平台对外宣传的常用键（见 MCP 能力的 `_PARAM_NOTE`）。
  > 若这里的键名和调用方按文档传的键不一致，`_collect_params` 会把它**静默丢掉**，
  > 调用方只会看到一句「请填写画面比例」，很难排查 —— 这是踩过的坑。
  > 像素串没法用 `aspectRatio` 表达，所以 `gpt-image` 系列仍走 `size`。
  >
  > 文档示例里出现的 `nano-banana-fast` **不在这个端点内**，所以没有列进清单。
  > 各型号的 `imageSize` 支持不一样，多传可能被上游拒绝，所以逐型号声明；
  > 账号如果开了别的型号，用配置界面的「自定义模型」补，不必改代码。

两个容易踩的坑：

1. **完成态响应没有 `status` 字段**，只给 `data[].url`（Images 协议）或
   `candidates[].content.parts[].text`（Gemini 协议）。所以 `_parse` 的判定顺序是
   「先看有没有结果，再看 status」，否则完成态会被当成进行中一直轮询。
2. **失败时 `error` 是对象** `{message, code}`（gsai 是字符串），`_extract_error` 两种都处理。

## 测试

- `tests/test_generation_providers.py`：注册表发现、目录按供应商隔离、RightAPI 适配器协议
  （含完成态无 status、Gemini 形态、嵌套 error）、端到端编排。
- 适配器测试通过替换 `httpx.Client` 来断言 URL、请求体与鉴权头，不发真实请求。
- `tests/test_generation.py` / `tests/test_mcp_generation.py` 覆盖编排层与 MCP 能力。
- 「新增一个模块就会被发现」这条承诺由
  `test_discovery_picks_up_a_brand_new_provider_module` 直接验证（用打桩的
  `iter_modules` / `import_module`，不会往包目录里真写文件）。

## 排障：生成成功但结果转存失败

现象：任务 `succeeded`，但结果资产 `failed`，错误是「结果地址不安全：链接指向内网地址，已拒绝」。

原因：结果的转存在 `service._download_result`，它对上游返回的地址逐跳调用
`assert_safe_url`（`app/info/urlguard.py`）做 SSRF 校验，**与供应商无关**。
如果本机 DNS 走了 fake-IP 模式（Clash/Surge 等把域名解析到 `198.18.0.0/15`
这类保留段），校验会正确地拒绝，于是**任何供应商**的结果都存不下来
（抖音 / 资讯媒体的转存同理）。

这不是代码缺陷，是 DNS 模式与安全校验的冲突。可选处理：

1. 把上游 CDN 域名加进代理的 `fake-ip-filter`，或改用 redir-host 模式；
2. 若出口确实经过可信代理、且接受放宽校验，再考虑加开关跳过私网检查 ——
   默认必须保持关闭，SSRF 防护是安全边界，不要为了跑通下载而默认放开。
