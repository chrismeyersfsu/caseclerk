"""Self-contained HTML report for the e2e happy-path run.

Records every MCP call (tool or prompt) and every assertion as the test
drives the real server over stdio, then renders one index.html (inline
CSS, no external assets) a human can open to see whether the app works.
"""

from __future__ import annotations

import html
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPORT_DIR_ENV = "CASECLERK_E2E_REPORT_DIR"
DEFAULT_REPORT_DIR = Path("test-artifacts") / "e2e"


def report_dir() -> Path:
    override = os.environ.get(REPORT_DIR_ENV)
    return Path(override) if override else DEFAULT_REPORT_DIR


@dataclass
class RecordedCall:
    kind: str  # "tool" | "prompt"
    name: str
    arguments: dict[str, Any]
    duration_ms: float
    is_error: bool
    result_snippet: str


@dataclass
class RecordedAssertion:
    description: str
    passed: bool
    detail: str = ""


@dataclass
class EmailSummary:
    subject: str
    recipient: str | None
    body: str
    eml_name: str
    txt_name: str


@dataclass
class Recorder:
    calls: list[RecordedCall] = field(default_factory=list)
    assertions: list[RecordedAssertion] = field(default_factory=list)

    def record_call(
        self,
        *,
        kind: str,
        name: str,
        arguments: dict[str, Any],
        duration_ms: float,
        is_error: bool,
        snippet: str,
    ) -> None:
        self.calls.append(
            RecordedCall(
                kind=kind,
                name=name,
                arguments=arguments,
                duration_ms=duration_ms,
                is_error=is_error,
                result_snippet=snippet,
            )
        )

    def check(self, description: str, condition: bool, detail: str = "") -> None:
        self.assertions.append(RecordedAssertion(description=description, passed=condition, detail=detail))
        assert condition, f"{description}: {detail}" if detail else description

    @property
    def all_assertions_passed(self) -> bool:
        return all(a.passed for a in self.assertions)


class Timer:
    """`with Timer() as t: ...` then `t.elapsed_ms`."""

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _snippet(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _render_calls_table(calls: list[RecordedCall]) -> str:
    if not calls:
        return "<p class='muted'>No calls were made.</p>"
    rows = []
    for i, call in enumerate(calls, start=1):
        status = "err" if call.is_error else "ok"
        status_label = "ERROR" if call.is_error else "ok"
        args_str = _esc(_snippet(str(call.arguments), 200))
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td><code>{_esc(call.kind)}</code></td>"
            f"<td><code>{_esc(call.name)}</code></td>"
            f"<td class='args'>{args_str}</td>"
            f"<td class='status {status}'>{status_label}</td>"
            f"<td class='num'>{call.duration_ms:.1f} ms</td>"
            f"<td class='snippet'><pre>{_esc(_snippet(call.result_snippet))}</pre></td>"
            "</tr>"
        )
    return (
        "<table class='calls'><thead><tr>"
        "<th>#</th><th>Kind</th><th>Name</th><th>Arguments</th><th>Status</th><th>Duration</th><th>Result</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_assertions_table(assertions: list[RecordedAssertion]) -> str:
    if not assertions:
        return "<p class='muted'>No assertions were recorded.</p>"
    rows = []
    for a in assertions:
        status = "ok" if a.passed else "err"
        label = "PASS" if a.passed else "FAIL"
        detail = f"<div class='muted'>{_esc(a.detail)}</div>" if a.detail else ""
        rows.append(
            f"<tr><td class='status {status}'>{label}</td><td>{_esc(a.description)}{detail}</td></tr>"
        )
    return (
        "<table class='assertions'><thead><tr><th>Result</th><th>Assertion</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_email_section(email: EmailSummary | None) -> str:
    if email is None:
        return "<p class='muted'>No email draft was produced.</p>"
    recipient = _esc(email.recipient) if email.recipient else "<span class='muted'>(none)</span>"
    return f"""
    <div class="email-card">
      <div class="email-header">
        <div><span class="label">To</span> {recipient}</div>
        <div><span class="label">Subject</span> {_esc(email.subject)}</div>
      </div>
      <pre class="email-body">{_esc(email.body)}</pre>
      <div class="email-links">
        <a href="{_esc(email.eml_name)}">{_esc(email.eml_name)}</a>
        &nbsp;&middot;&nbsp;
        <a href="{_esc(email.txt_name)}">{_esc(email.txt_name)}</a>
      </div>
    </div>
    """


_CSS = """
:root {
  --bg: #0f1420; --panel: #161d2e; --border: #2a3346; --text: #e7ecf5; --muted: #93a0bd;
  --ok: #35c98f; --err: #ff6b6b; --accent: #6c8cff;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 0.75rem; color: var(--accent); }
.muted { color: var(--muted); }
.banner {
  padding: 1rem 1.25rem; border-radius: 10px; font-size: 1.15rem; font-weight: 600;
  margin: 1rem 0 1.5rem; letter-spacing: 0.02em;
}
.banner.pass { background: rgba(53, 201, 143, 0.15); color: var(--ok); border: 1px solid var(--ok); }
.banner.fail { background: rgba(255, 107, 107, 0.15); color: var(--err); border: 1px solid var(--err); }
.narrative {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 1rem 1.25rem; font-style: italic;
}
.meta { color: var(--muted); font-size: 0.9rem; margin-top: 0.25rem; }
table {
  width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 10px; overflow: hidden;
}
table.calls, table.assertions { border: 1px solid var(--border); }
th, td {
  text-align: left; padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); vertical-align: top;
}
th {
  color: var(--muted); font-weight: 600; font-size: 0.82rem; text-transform: uppercase;
  letter-spacing: 0.04em;
}
tr:last-child td { border-bottom: none; }
td.num { text-align: right; white-space: nowrap; }
td.args, td.snippet { font-size: 0.85rem; max-width: 320px; }
td.snippet pre, .email-body {
  white-space: pre-wrap; word-break: break-word; margin: 0; font-family: inherit;
}
.status { font-weight: 700; font-size: 0.82rem; letter-spacing: 0.03em; }
.status.ok { color: var(--ok); }
.status.err { color: var(--err); }
code { background: rgba(255,255,255,0.06); padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.85em; }
.email-card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.25rem;
}
.email-header { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.75rem; }
.label { color: var(--muted); font-size: 0.8rem; text-transform: uppercase; margin-right: 0.4rem; }
.email-body { font-size: 0.95rem; border-top: 1px dashed var(--border); padding-top: 0.75rem; }
.email-links { margin-top: 0.75rem; font-size: 0.9rem; }
a { color: var(--accent); }
footer { margin-top: 2.5rem; color: var(--muted); font-size: 0.8rem; }
"""


def render_report(
    *,
    passed: bool,
    narrative: str,
    recorder: Recorder,
    email: EmailSummary | None,
    error_detail: str | None,
    started_at: str,
    dest_dir: Path | None = None,
) -> Path:
    """Write index.html (+ any copied .eml/.txt already placed in dest_dir) and return its path."""
    target = dest_dir if dest_dir is not None else report_dir()
    target.mkdir(parents=True, exist_ok=True)

    banner_class = "pass" if passed else "fail"
    banner_text = "PASS -- the happy path completed end to end" if passed else "FAIL -- see details below"
    error_html = f"<div class='banner fail'>{_esc(error_detail)}</div>" if error_detail else ""

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CaseClerk e2e report</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>CaseClerk end-to-end report</h1>
  <div class="meta">Run started {_esc(started_at)}</div>
  <div class="banner {banner_class}">{banner_text}</div>
  {error_html}

  <h2>Request</h2>
  <div class="narrative">&ldquo;{_esc(narrative)}&rdquo;</div>

  <h2>Assertions</h2>
  {_render_assertions_table(recorder.assertions)}

  <h2>MCP calls</h2>
  {_render_calls_table(recorder.calls)}

  <h2>Generated email</h2>
  {_render_email_section(email)}

  <footer>Generated by tests/e2e against a real `caseclerk serve` subprocess over stdio.</footer>
</div>
</body>
</html>
"""
    index_path = target / "index.html"
    index_path.write_text(body, encoding="utf-8")
    return index_path


def copy_artifact(source: Path, dest_dir: Path | None = None) -> str:
    """Copy a generated file (e.g. the .eml/.txt) next to the report; returns its filename."""
    target = dest_dir if dest_dir is not None else report_dir()
    target.mkdir(parents=True, exist_ok=True)
    destination = target / source.name
    shutil.copyfile(source, destination)
    return source.name
