from __future__ import annotations

import contextlib
from functools import lru_cache
from typing import Any

from app.config import get_settings


class ChromaSignatureMismatch(Exception):
    """集合里的向量由另一套 embedding 配置生成，必须重建后才能写入/检索。"""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"向量集合签名不一致：期望 {expected}，实际 {actual}")
        self.expected = expected
        self.actual = actual


@lru_cache
def _client(path: str):
    import chromadb

    return chromadb.PersistentClient(path=path)


def get_chroma_client():
    path = str(get_settings().resolved_chroma_path)
    return _client(path)


def reset_chroma_client_cache() -> None:
    _client.cache_clear()


def collection_name(kb_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kb_id)
    return f"kb_{safe}"[:63]


def _collection_exists(client, name: str) -> bool:
    return name in {item.name for item in client.list_collections()}


def collection_signature(kb_id: str) -> str | None:
    """返回集合中记录的 embedding 签名；集合不存在时返回 None。"""
    client = get_chroma_client()
    name = collection_name(kb_id)
    if not _collection_exists(client, name):
        return None
    metadata = client.get_collection(name=name).metadata or {}
    signature = metadata.get("signature")
    return str(signature) if signature else None


def _ensure_signature(client, name: str, signature: str) -> None:
    if not _collection_exists(client, name):
        return
    metadata = client.get_collection(name=name).metadata or {}
    actual = str(metadata.get("signature") or "")
    if actual != signature:
        raise ChromaSignatureMismatch(signature, actual)


def reset_collection_if_mismatched(kb_id: str, signature: str) -> str | None:
    """集合签名与目标不一致时清空集合，返回被替换的旧签名（无需重建则返回 None）。

    旧向量由另一套 embedding 配置生成，无法复用；清空后即可按新签名重建。
    """
    client = get_chroma_client()
    name = collection_name(kb_id)
    if not _collection_exists(client, name):
        return None
    metadata = client.get_collection(name=name).metadata or {}
    actual = str(metadata.get("signature") or "")
    if actual == signature:
        return None
    client.delete_collection(name=name)
    return actual


def upsert_chunks(
    kb_id: str,
    *,
    signature: str,
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
) -> str | None:
    """写入向量；若集合签名与目标不一致则先清空集合重建，返回被替换的旧签名。"""
    if not ids:
        return None
    client = get_chroma_client()
    name = collection_name(kb_id)
    replaced = reset_collection_if_mismatched(kb_id, signature)
    coll = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine", "signature": signature},
    )
    coll.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    return replaced


def query_chunks(
    kb_id: str,
    *,
    signature: str,
    embedding: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    client = get_chroma_client()
    name = collection_name(kb_id)
    if not _collection_exists(client, name):
        return []
    _ensure_signature(client, name, signature)
    coll = client.get_collection(name=name)
    count = coll.count()
    if count == 0:
        return []
    result = coll.query(query_embeddings=[embedding], n_results=min(top_k, max(1, count)))
    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    hits: list[dict[str, Any]] = []
    for index, chunk_id in enumerate(ids):
        distance = float(distances[index]) if index < len(distances) else 1.0
        score = 1.0 / (1.0 + max(0.0, distance))
        meta = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        hits.append(
            {
                "chunk_id": chunk_id,
                "document_id": str(meta.get("document_id") or ""),
                "text": documents[index] if index < len(documents) else "",
                "score": score,
                "source_name": str(meta.get("source_name") or ""),
            }
        )
    return hits


def delete_ids(kb_id: str, ids: list[str]) -> None:
    if not ids:
        return
    client = get_chroma_client()
    name = collection_name(kb_id)
    if not _collection_exists(client, name):
        return
    client.get_collection(name=name).delete(ids=ids)


def delete_collection(kb_id: str) -> None:
    client = get_chroma_client()
    name = collection_name(kb_id)
    with contextlib.suppress(Exception):
        client.delete_collection(name=name)
