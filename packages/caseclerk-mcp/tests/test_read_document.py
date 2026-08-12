from __future__ import annotations

from conftest import Env

from caseclerk_core import db


def test_read_document_single_page(env: Env) -> None:
    _case_id, doc_ids = env.seed_case("Alvarez, Maria", "2026-0142", [("letter.md", "hello world")])
    result = env.call(
        "read_document", client="Alvarez, Maria", case_number="2026-0142", document_id=doc_ids[0]
    )
    payload = result.structured_content
    assert payload["has_more"] is False
    assert payload["next_start_seq"] is None
    assert len(payload["chunks"]) == 1
    assert payload["chunks"][0]["text"] == "hello world"


def test_read_document_pages_across_multiple_chunks(env: Env) -> None:
    _case_id, doc_ids = env.seed_case("Alvarez, Maria", "2026-0142", [("letter.md", "seed")])
    doc_id = doc_ids[0]

    conn = env.open_db()
    try:
        db.replace_chunks(conn, doc_id, [(seq, f"chunk number {seq}", 5) for seq in range(7)])
    finally:
        conn.close()

    first = env.call(
        "read_document",
        client="Alvarez, Maria",
        case_number="2026-0142",
        document_id=doc_id,
        start_seq=0,
        max_chunks=3,
    )
    first_payload = first.structured_content
    assert [c["seq"] for c in first_payload["chunks"]] == [0, 1, 2]
    assert first_payload["has_more"] is True
    assert first_payload["next_start_seq"] == 3

    second = env.call(
        "read_document",
        client="Alvarez, Maria",
        case_number="2026-0142",
        document_id=doc_id,
        start_seq=first_payload["next_start_seq"],
        max_chunks=3,
    )
    second_payload = second.structured_content
    assert [c["seq"] for c in second_payload["chunks"]] == [3, 4, 5]
    assert second_payload["has_more"] is True
    assert second_payload["next_start_seq"] == 6

    third = env.call(
        "read_document",
        client="Alvarez, Maria",
        case_number="2026-0142",
        document_id=doc_id,
        start_seq=second_payload["next_start_seq"],
        max_chunks=3,
    )
    third_payload = third.structured_content
    assert [c["seq"] for c in third_payload["chunks"]] == [6]
    assert third_payload["has_more"] is False
    assert third_payload["next_start_seq"] is None
