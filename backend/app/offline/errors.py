"""离线下载的统一错误类型。"""

from __future__ import annotations


class OfflineError(Exception):
    """面向调用方的可预期错误；路由层据此转换成对应的 HTTP 状态码。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_type: str = "offline_error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


class UpstreamError(OfflineError):
    """上游服务异常（网络超时、非 2xx 等）。"""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message, status_code=status_code, error_type="upstream_error")
