from __future__ import annotations


def split_text(text: str, chunk_size: int = 700, chunk_overlap: int = 100) -> list[str]:
    normalized = (text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    size = max(100, int(chunk_size))
    overlap = max(0, min(int(chunk_overlap), size // 2))
    if len(normalized) <= size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = normalized[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind("。"), window.rfind(". "))
            if break_at >= size // 3:
                end = start + break_at + 1
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks
