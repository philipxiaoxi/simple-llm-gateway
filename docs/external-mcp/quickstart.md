# 外部 MCP 服务接入教程（Python）

外部进程只向平台发出 HTTPS 请求，不需要开放入站端口。已有 MCP 服务无需配置迁移或重启。

## 1. 签发注册凭证

进入 **MCP 广场 → 外部服务**，创建服务，输入部署凭证名称并点「签发凭证」。把只显示一次的 `mcpreg-…` 保存到部署环境的 Secret 管理器。它不是供客户端调用使用的 `mcp-…` Key。

## 2. 启动示例

Python 3.11+，在外部服务器创建独立虚拟环境，安装 `httpx==0.28.1`。从仓库取出 `sdk/python/external_mcp.py` 和 `sdk/python/echo.py` 放在同一目录。设置环境变量后运行：

```bash
export MCP_PLATFORM_URL=https://gateway.example.com
# MCP_REGISTRATION_TOKEN 由部署系统注入，不要提交到 Git 或输出到日志
python echo.py
```

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
- 「移除当前注册」只清理平台租约。尚在运行的 worker 发现旧租约失效后会自己注册回来，无需重启原 MCP 服务。
- 想让特定部署不再注册，请销毁它使用的凭证。轮换时先发新凭证，更新部署并确认上线，再销毁旧凭证。续期有效凭证不需要更换 Token。
- 工具清单变更后，新版本需在管理页批准才能上线。SDK 会退避重试；批准新版本会让旧版本实例停止接单。

## 5. 接入已有的 MCP Server（可选）

已有服务保持原样。在同一主机另起 `sdk/python/bridge.py`，额外安装 `mcp==1.13.1`。设置 `LOCAL_MCP_URL=http://127.0.0.1:9000/mcp`（替换成本机实际地址）和工具白名单 `MCP_BRIDGE_TOOL_MAP=echo=local_echo`，如本机服务需要鉴权，通过 Secret 注入 `LOCAL_MCP_TOKEN`。启动桥接器前仍需配置步骤 2 的平台地址和注册凭证。左侧是平台操作名，右侧是原工具名；不在白名单内的工具不会注册。首版桥接支持 Streamable HTTP，输入 schema 需符合接入协议限制。

协议详情见[设计与接口](../external-mcp-services.md)。机器可读的控制面接口为 `GET /v1/external-mcp/openapi.json`。首版不代理远程资源、提示词或透明 MCP 会话。
