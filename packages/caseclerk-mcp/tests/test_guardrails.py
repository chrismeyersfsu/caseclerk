import pytest
from conftest import Env
from mcp.server.mcpserver.exceptions import ToolError


def test_unknown_client_raises_clean_message(env: Env) -> None:
    with pytest.raises(ToolError, match="Unknown client 'Nobody'"):
        env.call("list_cases", client="Nobody")


def test_unknown_client_on_case_overview(env: Env) -> None:
    with pytest.raises(ToolError, match="Unknown client 'Nobody'"):
        env.call("get_case_overview", client="Nobody", case_number="2026-0142")


def test_wrong_client_case_pairing_raises_clean_message(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142", [("letter.txt", "hello")])
    env.seed_case("Barrett Holdings LLC", "2026-0201", [("letter.txt", "hi")])

    # a real case_number, but paired with a client it doesn't belong to
    with pytest.raises(ToolError, match="No case '2026-0142' for client 'Barrett Holdings LLC'"):
        env.call("get_case_overview", client="Barrett Holdings LLC", case_number="2026-0142")


def test_error_message_contains_no_stack_trace_or_path(env: Env) -> None:
    with pytest.raises(ToolError) as exc_info:
        env.call("list_cases", client="Nobody")
    message = str(exc_info.value)
    assert "Traceback" not in message
    assert "/tmp" not in message
    assert str(env.tmp_path) not in message


def test_cross_client_search_never_leaks(env: Env) -> None:
    case_a, _ = env.seed_case(
        "Alvarez, Maria", "2026-0142", [("depo.md", "The Tampa conference alibi is disputed.")]
    )
    case_b, _ = env.seed_case(
        "Barrett Holdings LLC", "2026-0201", [("depo.md", "The Tampa conference alibi is disputed.")]
    )

    result_a = env.call("search_case", client="Alvarez, Maria", case_number="2026-0142", queries=["Tampa"])
    result_b = env.call(
        "search_case", client="Barrett Holdings LLC", case_number="2026-0201", queries=["Tampa"]
    )

    hits_a = result_a.structured_content["result"]
    hits_b = result_b.structured_content["result"]
    assert len(hits_a) == 1
    assert len(hits_b) == 1
    assert hits_a[0]["document_id"] != hits_b[0]["document_id"]


def test_cross_client_overview_never_leaks(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142", [("secret-a.md", "client A only")])
    env.seed_case("Barrett Holdings LLC", "2026-0201", [("secret-b.md", "client B only")])

    result = env.call("get_case_overview", client="Alvarez, Maria", case_number="2026-0142")
    file_names = [d["file_name"] for d in result.structured_content["documents"]]
    assert file_names == ["secret-a.md"]


def test_cross_client_read_document_by_id_is_rejected(env: Env) -> None:
    _case_a, docs_a = env.seed_case("Alvarez, Maria", "2026-0142", [("a.md", "client A text")])
    case_b, _docs_b = env.seed_case("Barrett Holdings LLC", "2026-0201", [("b.md", "client B text")])

    # a client B request can't read a document that actually belongs to client A,
    # even though it supplies a syntactically valid client/case pair of its own
    with pytest.raises(ToolError, match="No document"):
        env.call(
            "read_document",
            client="Barrett Holdings LLC",
            case_number="2026-0201",
            document_id=docs_a[0],
        )


def test_reprocess_document_rejects_cross_case_id(env: Env) -> None:
    _case_a, docs_a = env.seed_case("Alvarez, Maria", "2026-0142", [("a.md", "text")])
    env.seed_case("Barrett Holdings LLC", "2026-0201", [("b.md", "text")])

    with pytest.raises(ToolError, match="No document"):
        env.call(
            "reprocess_document",
            client="Barrett Holdings LLC",
            case_number="2026-0201",
            document_id=docs_a[0],
        )
