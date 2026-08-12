from caseclerk_pipeline.chunker import chunk_markdown, estimate_tokens


def test_empty_text_produces_no_chunks() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_short_text_is_a_single_chunk() -> None:
    chunks = chunk_markdown("First paragraph.\n\nSecond paragraph.")
    assert len(chunks) == 1
    assert chunks[0].seq == 0
    assert "First paragraph." in chunks[0].text
    assert "Second paragraph." in chunks[0].text


def test_chunks_prefer_paragraph_boundaries() -> None:
    # each paragraph ~40 chars; force a small target so paragraphs must split across chunks
    paragraphs = [f"Paragraph number {i} with some filler words here." for i in range(20)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_markdown(text, target_tokens=25)  # ~100 chars per chunk
    assert len(chunks) > 1
    for chunk in chunks:
        for paragraph in paragraphs:
            # a paragraph that appears must appear whole (not split mid-paragraph)
            if paragraph in chunk.text:
                assert chunk.text.count(paragraph) >= 1

    # every paragraph must show up somewhere across the chunks, exactly once total
    reassembled = "\n\n".join(c.text for c in chunks)
    for paragraph in paragraphs:
        assert reassembled.count(paragraph) == 1


def test_sequence_numbers_are_contiguous() -> None:
    text = "\n\n".join(f"Paragraph {i}." for i in range(10))
    chunks = chunk_markdown(text, target_tokens=5)
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_oversized_single_paragraph_is_hard_split() -> None:
    huge_paragraph = "word " * 2000  # far bigger than any reasonable target
    chunks = chunk_markdown(huge_paragraph, target_tokens=50)  # target_chars = 200
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 200


def test_estimate_tokens_uses_chars_over_four_heuristic() -> None:
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1  # never zero, avoids degenerate empty chunks
