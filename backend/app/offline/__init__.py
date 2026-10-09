"""离线下载（离线资源获取）提供商包。

把 VSCode 插件、Chrome/Edge 扩展、Docker 镜像、Microsoft Store 应用等公开资源，
解析成可离线使用的安装包或下载链接。每个来源一个模块，集中登记到 `registry`，
路由层只做参数校验与错误转换，便于后续新增来源。
"""

from app.offline.registry import PROVIDERS, get_provider

__all__ = ["PROVIDERS", "get_provider"]
