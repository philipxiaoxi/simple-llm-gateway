from __future__ import annotations

from dataclasses import dataclass


class KnowledgeError(Exception):
    """业务错误：带 HTTP 状态与错误类型，供 REST / MCP 统一映射。"""

    def __init__(self, message: str, status_code: int = 400, error_type: str = "invalid_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


@dataclass
class SearchHit:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source_name: str
    kb_id: str = ""


class JobCanceled(Exception):
    """运行中的采集任务被取消。"""
