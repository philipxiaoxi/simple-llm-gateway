"""知识库领域服务。

按职责拆分：
- access  访问范围（public / restricted / private）与 MCP Key 白名单
- bases   知识库元数据与向量绑定
- documents 文档元数据与删除
- fts     全文索引读写（SQLite FTS5）
- ingest  入库与向量化
- search  全文 / 向量 / RRF 混合检索

对外保持既有导入路径：`from app.services import knowledge as knowledge_service`。
"""

from app.services.knowledge.access import (
    SCOPE_PRIVATE,
    SCOPE_PUBLIC,
    SCOPE_RESTRICTED,
    VALID_SCOPES,
    Access,
    allowed_key_ids,
    assert_can_access,
    assert_scope_valid,
    can_access,
    set_allowed_keys,
)
from app.services.knowledge.bases import (
    base_signature,
    create_base,
    delete_base,
    get_base,
    get_base_for_access,
    list_bases,
    mark_documents_stale,
    update_base,
    validate_embedding_binding,
)
from app.services.knowledge.common import account_name, emit, new_id
from app.services.knowledge.documents import (
    delete_document,
    find_by_hash,
    get_document,
    list_document_ids,
    list_documents,
)
from app.services.knowledge.errors import JobCanceled, KnowledgeError, SearchHit
from app.services.knowledge.ingest import (
    PrepareResult,
    add_document,
    content_digest,
    document_chunks,
    embed_document_chunks,
    prepare_document,
    reembed_document,
)
from app.services.knowledge.search import (
    fulltext_hits,
    resolve_kb_ids,
    rrf_merge,
    search,
    vector_hits,
)

__all__ = [
    "Access",
    "JobCanceled",
    "KnowledgeError",
    "PrepareResult",
    "SCOPE_PRIVATE",
    "SCOPE_PUBLIC",
    "SCOPE_RESTRICTED",
    "SearchHit",
    "VALID_SCOPES",
    "account_name",
    "add_document",
    "assert_can_access",
    "assert_scope_valid",
    "allowed_key_ids",
    "base_signature",
    "can_access",
    "content_digest",
    "create_base",
    "delete_base",
    "delete_document",
    "document_chunks",
    "embed_document_chunks",
    "emit",
    "find_by_hash",
    "fulltext_hits",
    "get_base",
    "get_base_for_access",
    "get_document",
    "list_bases",
    "list_document_ids",
    "list_documents",
    "mark_documents_stale",
    "new_id",
    "prepare_document",
    "reembed_document",
    "resolve_kb_ids",
    "rrf_merge",
    "search",
    "set_allowed_keys",
    "update_base",
    "validate_embedding_binding",
    "vector_hits",
]
