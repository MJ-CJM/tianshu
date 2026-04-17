"""Verbatim text chunker — paragraph-boundary splitting at ~800 chars."""

from __future__ import annotations

_DEFAULT_MAX = 800
_DEFAULT_MIN = 10


def chunk_text(
    text: str,
    max_chars: int = _DEFAULT_MAX,
    min_chars: int = _DEFAULT_MIN,
) -> list[str]:
    """Split text into chunks at paragraph boundaries.

    Strategy:
    1. Split on double-newline (paragraph boundary)
    2. If a paragraph exceeds max_chars, force-split at max_chars
    3. Merge small consecutive paragraphs into one chunk
    4. Drop chunks shorter than min_chars
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if not current:
            current = para
        elif len(current) + 2 + len(para) <= max_chars:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    # Force-split oversized chunks
    final: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            final.append(chunk[:max_chars])
            chunk = chunk[max_chars:]
        if chunk:
            final.append(chunk)

    # Filter by min size
    return [c for c in final if len(c) >= min_chars]
