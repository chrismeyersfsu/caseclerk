"""Split extracted markdown into ~500-token chunks, preferring paragraph boundaries."""

from __future__ import annotations

from dataclasses import dataclass

CHARS_PER_TOKEN = 4
DEFAULT_TARGET_TOKENS = 500


@dataclass(frozen=True)
class TextChunk:
    seq: int
    text: str
    token_estimate: int


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def chunk_markdown(text: str, *, target_tokens: int = DEFAULT_TARGET_TOKENS) -> list[TextChunk]:
    """Greedily pack paragraphs into chunks near target_tokens; hard-splits an oversized paragraph."""
    target_chars = target_tokens * CHARS_PER_TOKEN
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[TextChunk] = []
    current: list[str] = []
    current_len = 0
    seq = 0

    def flush() -> None:
        nonlocal current, current_len, seq
        if not current:
            return
        chunk_text = "\n\n".join(current)
        chunks.append(TextChunk(seq=seq, text=chunk_text, token_estimate=estimate_tokens(chunk_text)))
        seq += 1
        current = []
        current_len = 0

    for paragraph in paragraphs:
        if current and current_len + len(paragraph) + 2 > target_chars:
            flush()
        if len(paragraph) > target_chars:
            flush()
            for start in range(0, len(paragraph), target_chars):
                piece = paragraph[start : start + target_chars]
                chunks.append(TextChunk(seq=seq, text=piece, token_estimate=estimate_tokens(piece)))
                seq += 1
            continue
        current.append(paragraph)
        current_len += len(paragraph) + 2

    flush()
    return chunks
