"""资讯收集：把公开渠道的内容采集进本地库，供管理端瀑布流浏览。

采集与浏览链路与 capabilities/ 隔离；仅「上报」这一条写入口经
`capabilities/info/provider.py` 注册进能力平面（MCP + REST），
详见 `.monkeycode/specs/2026-10-09-info-report-capability/`。
"""
