"""End-to-end happy path: spawn the real `caseclerk serve` over stdio and drive
the plan's demo scenario -- an email about the April 21, 2026 deposition
conflict, addressed to opposing counsel -- exactly as an MCP host would.

Deterministic by construction: `caseclerk process` runs synchronously to
completion before the server is even spawned, so the server's own
startup-scan thread finds nothing to do and there is no indexing race to
poll for.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp_types import CallToolResult, GetPromptResult, TextContent
from report import EmailSummary, Recorder, Timer, copy_artifact, render_report

from caseclerk_fixtures import (
    CONFLICT_DATE_ISO,
    DEMO_CASE,
    DEMO_CLIENT,
    OTHER_CLIENT,
    OTHER_CLIENT_CASE_1,
    build_fixture_drive,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NARRATIVE = (
    "Send an email to client's lawyer. Tell him the conflict that the April 21, 2026 "
    "deposition revealed and ask him if he can account for where his client was."
)
EXPECTED_TOOL_NAMES = {
    "list_clients",
    "list_cases",
    "get_case_overview",
    "search_case",
    "read_document",
    "save_email_draft",
    "processing_status",
    "list_processing_failures",
    "reprocess_document",
    "get_settings",
}
PROCESS_TIMEOUT_SECONDS = 120


def _caseclerk_binary() -> Path:
    venv_bin = Path(sys.executable).parent
    name = "caseclerk.exe" if sys.platform == "win32" else "caseclerk"
    binary = venv_bin / name
    if not binary.is_file():
        raise RuntimeError(f"caseclerk console script not found at {binary}; run `uv sync --all-packages`")
    return binary


def _tool_text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def _prompt_text(result: GetPromptResult) -> str:
    if not result.messages:
        return ""
    content = result.messages[0].content
    return content.text if isinstance(content, TextContent) else ""


async def _call_tool(
    client: Client, recorder: Recorder, name: str, arguments: dict[str, object]
) -> CallToolResult:
    with Timer() as timer:
        result = await client.call_tool(name, arguments)
    snippet = str(result.structured_content) if result.structured_content is not None else _tool_text(result)
    recorder.record_call(
        kind="tool",
        name=name,
        arguments=arguments,
        duration_ms=timer.elapsed_ms,
        is_error=bool(result.is_error),
        snippet=snippet,
    )
    return result


async def _run(tmp_path: Path) -> None:
    started_at = datetime.now(UTC).isoformat()
    recorder = Recorder()
    email_summary: EmailSummary | None = None
    passed = False
    error_detail: str | None = None

    clio_root = build_fixture_drive(tmp_path / "clio")
    env_overrides = {
        "CASECLERK_CONFIG_DIR": str(tmp_path / "config"),
        "CASECLERK_DATA_DIR": str(tmp_path / "data"),
        "CASECLERK_CLIO_ROOT": str(clio_root),
    }
    caseclerk_bin = _caseclerk_binary()

    try:
        process_result = subprocess.run(
            [str(caseclerk_bin), "process"],
            env={**os.environ, **env_overrides},
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        recorder.check(
            "`caseclerk process` indexed the fixture drive ahead of time",
            process_result.returncode == 0,
            (process_result.stdout + process_result.stderr).strip(),
        )

        params = StdioServerParameters(
            command=str(caseclerk_bin), args=["serve"], env=env_overrides, cwd=str(REPO_ROOT)
        )
        async with Client(server=stdio_client(params)) as client:
            with Timer() as timer:
                tools = await client.list_tools()
            tool_names = {t.name for t in tools.tools}
            recorder.record_call(
                kind="tool",
                name="tools/list",
                arguments={},
                duration_ms=timer.elapsed_ms,
                is_error=False,
                snippet=", ".join(sorted(tool_names)),
            )
            recorder.check(
                "all 10 tools are exposed", tool_names == EXPECTED_TOOL_NAMES, str(sorted(tool_names))
            )

            with Timer() as timer:
                prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts.prompts}
            recorder.record_call(
                kind="prompt",
                name="prompts/list",
                arguments={},
                duration_ms=timer.elapsed_ms,
                is_error=False,
                snippet=", ".join(sorted(prompt_names)),
            )
            recorder.check(
                "the draft-email prompt is exposed", "draft-email" in prompt_names, str(prompt_names)
            )

            status = await _call_tool(client, recorder, "processing_status", {})
            counts = (status.structured_content or {}).get("by_state", {})
            recorder.check(
                "the fixture drive finished indexing before any tool call (0 pending/processing)",
                counts.get("pending", 0) == 0 and counts.get("processing", 0) == 0,
                str(counts),
            )

            overview = await _call_tool(
                client, recorder, "get_case_overview", {"client": DEMO_CLIENT, "case_number": DEMO_CASE}
            )
            recorder.check("get_case_overview succeeded", not overview.is_error, _tool_text(overview))
            documents = (overview.structured_content or {}).get("documents", [])
            depo_doc = next((d for d in documents if d["file_name"] == "deposition-transcript.docx"), None)
            recorder.check("the deposition transcript appears in the overview", depo_doc is not None)

            search = await _call_tool(
                client,
                recorder,
                "search_case",
                {
                    "client": DEMO_CLIENT,
                    "case_number": DEMO_CASE,
                    "queries": ["deposition", "conflict"],
                    "date": CONFLICT_DATE_ISO,
                },
            )
            recorder.check("search_case succeeded", not search.is_error, _tool_text(search))
            hit_files = {h["file_name"] for h in (search.structured_content or {}).get("result", [])}
            recorder.check(
                "search hits include the deposition transcript",
                "deposition-transcript.docx" in hit_files,
                str(hit_files),
            )
            recorder.check(
                "search hits include the security report", "security-report.pdf" in hit_files, str(hit_files)
            )

            assert depo_doc is not None
            read = await _call_tool(
                client,
                recorder,
                "read_document",
                {"client": DEMO_CLIENT, "case_number": DEMO_CASE, "document_id": depo_doc["id"]},
            )
            recorder.check("read_document succeeded", not read.is_error, _tool_text(read))
            read_text = " ".join(c["text"] for c in (read.structured_content or {}).get("chunks", []))
            recorder.check("the deposition text mentions the Tampa alibi", "Tampa" in read_text)

            subject = "Deposition conflict -- April 21, 2026"
            body = (
                "The April 21, 2026 deposition transcript has your client stating he was at a "
                "work conference in Tampa, Florida all day. The security log for the Clearwater "
                "property places him on-site there for most of that same day. Can your client "
                "account for where he actually was on April 21, 2026?"
            )
            recipient = "calloway@example-firm.com"
            email_result = await _call_tool(
                client,
                recorder,
                "save_email_draft",
                {
                    "client": DEMO_CLIENT,
                    "case_number": DEMO_CASE,
                    "subject": subject,
                    "body": body,
                    "slug": "deposition-conflict",
                    "recipient": recipient,
                    "citations": ["deposition-transcript.docx", "security-report.pdf"],
                },
            )
            recorder.check("save_email_draft succeeded", not email_result.is_error, _tool_text(email_result))
            paths = email_result.structured_content or {}
            eml_path = Path(paths["eml_path"]) if "eml_path" in paths else None
            txt_path = Path(paths["txt_path"]) if "txt_path" in paths else None

            recorder.check("the .eml file exists", eml_path is not None and eml_path.is_file(), str(eml_path))
            recorder.check("the .txt file exists", txt_path is not None and txt_path.is_file(), str(txt_path))
            if eml_path is not None and txt_path is not None:
                expected_dir = clio_root / DEMO_CLIENT / DEMO_CASE / "emails-generated"
                recorder.check(
                    "both files landed in the fixture case's emails-generated/ folder",
                    eml_path.parent == expected_dir and txt_path.parent == expected_dir,
                    f"{eml_path.parent} / {txt_path.parent} vs {expected_dir}",
                )
                date_prefix, _, name_suffix = eml_path.stem.partition("-deposition-conflict")
                recorder.check(
                    "the filename matches YYYY-MM-DD-deposition-conflict",
                    len(date_prefix) == len("YYYY-MM-DD") and name_suffix == "",
                    eml_path.stem,
                )

                eml_bytes = eml_path.read_bytes()
                recorder.check("the .eml carries X-Unsent: 1", b"X-Unsent: 1" in eml_bytes)
                recorder.check(
                    "the .eml has a To header for opposing counsel", f"To: {recipient}".encode() in eml_bytes
                )
                recorder.check("the .eml carries the Subject", subject.encode() in eml_bytes)
                recorder.check(
                    "the .eml uses CRLF line endings",
                    b"\n" in eml_bytes and eml_bytes.count(b"\n") == eml_bytes.count(b"\r\n"),
                )

                eml_copy_name = copy_artifact(eml_path)
                txt_copy_name = copy_artifact(txt_path)
                email_summary = EmailSummary(
                    subject=subject,
                    recipient=recipient,
                    body=body,
                    eml_name=eml_copy_name,
                    txt_name=txt_copy_name,
                )

            with Timer() as timer:
                prompt = await client.get_prompt(
                    "draft-email", {"client": DEMO_CLIENT, "case_number": DEMO_CASE, "request": NARRATIVE}
                )
            prompt_text = _prompt_text(prompt)
            recorder.record_call(
                kind="prompt",
                name="draft-email",
                arguments={"client": DEMO_CLIENT, "case_number": DEMO_CASE},
                duration_ms=timer.elapsed_ms,
                is_error=False,
                snippet=prompt_text,
            )
            recorder.check("the draft-email prompt mentions this case number", DEMO_CASE in prompt_text)

            # -- negative guardrail probes over the real wire --
            cross_overview = await _call_tool(
                client,
                recorder,
                "get_case_overview",
                {"client": DEMO_CLIENT, "case_number": OTHER_CLIENT_CASE_1},
            )
            recorder.check(
                "a case belonging to another client is rejected, not silently served",
                cross_overview.is_error is True,
                _tool_text(cross_overview),
            )

            cross_search = await _call_tool(
                client,
                recorder,
                "search_case",
                {"client": OTHER_CLIENT, "case_number": OTHER_CLIENT_CASE_1, "queries": ["Whitfield"]},
            )
            recorder.check(
                "search_case for another client never returns this case's content",
                not cross_search.is_error and cross_search.structured_content == {"result": []},
                str(cross_search.structured_content),
            )

        passed = recorder.all_assertions_passed
    except Exception as exc:  # noqa: BLE001 - the report must render even when a step fails
        passed = False
        error_detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        render_report(
            passed=passed,
            narrative=NARRATIVE,
            recorder=recorder,
            email=email_summary,
            error_detail=error_detail,
            started_at=started_at,
        )


@pytest.mark.e2e
def test_happy_path(tmp_path: Path) -> None:
    asyncio.run(_run(tmp_path))
