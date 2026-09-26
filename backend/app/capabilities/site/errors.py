from __future__ import annotations


class SiteError(Exception):
    def __init__(self, message: str, status_code: int = 400, error_type: str = "invalid_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
