# 外部 MCP 服务接入教程（Python / Node.js）

外部进程只向平台发出 HTTPS 请求，不需要开放入站端口。已有 MCP 服务无需配置迁移或重启。

## 1. 签发注册凭证

进入 **MCP 广场 → 外部服务**，创建服务，再打开「签发凭证」。注册凭证默认永不过期，也可选择 7、30 或 90 天。把只显示一次的 `mcpreg-…` 保存到部署环境的密钥管理器；不再使用或疑似泄露时请手动销毁。它不是供客户端调用使用的 `mcp-…` Key。

## 2. 启动示例

在 **MCP 广场 → 外部服务** 点击「下载接入包」，解压 `external-mcp-sdk.zip`。按部署环境选择其中一份即可：

```bash
export MCP_PLATFORM_URL=https://gateway.example.com
# MCP_REGISTRATION_TOKEN 由部署系统注入，不要提交到 Git 或输出到日志
```

**Python 3.11+**：在外部服务器创建并启用虚拟环境，运行 `python -m pip install -r python/requirements.txt`，再执行 `python python/echo.py`。直接使用源码仓库时，入口是 `sdk/python/echo.py`，依赖 `httpx==0.28.1`。

**Node.js 22+**：执行 `node nodejs/echo.mjs`，无需安装 npm 依赖。直接使用源码仓库时，入口是 `sdk/nodejs/echo.mjs`。用 `worker.tool(definition, handler)` 声明工具；处理器收到 `arguments` 和 `{ signal, callId, deadlineAt }`，返回 JSON 对象。对可取消的耗时操作应传递 `signal`；不要在事件循环中运行长时间的同步代码，以免阻塞心跳。

**交付文件**：在 Python 的 `async def` 工具处理器中调用 `await worker.upload_file(pptx_path, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")`。在 Node.js 处理器中，从第二个参数取 `uploadFile`，调用 `await uploadFile(await openAsBlob(pptxPath), { filename: 'slides.pptx', contentType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' })`（`openAsBlob` 从 `node:fs` 导入）。然后照常返回业务 JSON；SDK 会自动随成功结果上报文件 ID，网关再生成下载链接。具体限制见[文件交付协议](../external-mcp-services.md#可选交付文件pptxpdf-等)。

`MCP_PLATFORM_URL` 是平台基地址，不带 `/mcp` 或 `/v1` 后缀；本机开发允许 `http://127.0.0.1:8000`。Caddy/Nginx 等生产代理需保留 `Authorization` 请求头、将 `/v1/external-mcp/` 转发至后端，并允许至少 25 秒读取超时。开发预览若带有 VS Code 登录门禁，不是外部无人值守部署入口。

`@worker.tool(...)` 声明操作名、说明及对象类型 JSON Schema；函数接收 schema 描述的命名参数，返回 JSON 对象。可加 `example` 和 `returns` 自动生成接入说明。可声明 `timeout_seconds`（默认 20 秒）和 `side_effects`；SDK 不会自动重跑已领取的操作。建议把长任务拆成提交、状态、结果三个操作。优先使用异步处理器；同步处理器超时后其后台线程不能被强制停止。

## 3. 调用与接入说明

1. 管理页显示「在线」后，在「MCP Key」给一个现有 Key 授权该外部能力。
2. 打开该 Key 的「接入说明」。MCP 与 REST 配置都使用平台地址，SDK 示例中的参数会出现在 REST 调用示例中。
3. 标准 MCP 客户端连接平台 `/mcp`，使用消费侧 `mcp-…` Key 执行 `tools/list`，然后调用 `ext_<服务ID>_echo`。REST 可使用接入说明生成的路径：

```bash
curl -sS "$MCP_PLATFORM_URL/v1/capabilities/ext_<服务ID>/echo" \
  -H "Authorization: Bearer $MCP_CONSUMER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"text":"hello"}'
```

## 4. 下线、恢复与凭证管理

- Ctrl+C 正常退出时 SDK 尝试释放租约；进程失联时最长 45 秒失效。离线能力不可调用，不会丢失原有 Key 授权。
- 服务详情的「心跳历史」可查看最近 1 小时（每格 1 分钟）或 24 小时（每格 15 分钟）。绿色表示该时段收到注册或续租，橙色表示未收到，灰色表示当时尚未开始记录；聚合次数不等同于连续在线时长。
- 「移除当前注册」只清理平台租约。尚在运行的 worker 发现旧租约失效后会自己注册回来，无需重启原 MCP 服务。
- 「编辑名称」只改变显示名，不改变能力 ID、凭证或已有授权。「删除服务」会从平台移除能力并撤销它的全部注册凭证；不停止外部进程。重新接入需要创建新服务、签发新凭证和重新授权。
- 想让特定部署不再注册，请销毁它使用的凭证。轮换时先发新凭证，更新部署并确认上线，再销毁旧凭证。续期有效凭证不需要更换 Token。
- 工具清单变更后，新版本需在管理页批准才能上线。SDK 会退避重试；批准新版本会让旧版本实例停止接单。

## 5. 接入已有的 MCP Server（可选）

已有服务保持原样。在同一主机另起 `sdk/python/bridge.py`，额外安装 `mcp==1.13.1`。设置 `LOCAL_MCP_URL=http://127.0.0.1:9000/mcp`（替换成本机实际地址）和工具白名单 `MCP_BRIDGE_TOOL_MAP=echo=local_echo`，如本机服务需要鉴权，通过 Secret 注入 `LOCAL_MCP_TOKEN`。启动桥接器前仍需配置步骤 2 的平台地址和注册凭证。左侧是平台操作名，右侧是原工具名；不在白名单内的工具不会注册。首版桥接支持 Streamable HTTP，输入 schema 需符合接入协议限制。

协议详情见[设计与接口](../external-mcp-services.md)。机器可读的控制面接口为 `GET /v1/external-mcp/openapi.json`。首版不代理远程资源、提示词或透明 MCP 会话。
