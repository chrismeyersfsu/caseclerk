"""The `caseclerk` console script: serve, init, process, status, failures,
retry, config, update, doctor.

Command output is for a human on a terminal, so plain `typer.echo` is fine
everywhere here -- the stdout discipline (never print, always stderr-log)
only applies to `serve`, which hands off entirely to the MCP stdio server.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import typer

from caseclerk_cli import config_commands
from caseclerk_cli.claude_desktop import SERVER_ENTRY, claude_desktop_config_path, write_claude_desktop_entry
from caseclerk_core import db, scan
from caseclerk_core import update as core_update
from caseclerk_core.config import load_config, save_config
from caseclerk_core.discovery import discover
from caseclerk_core.paths import PathContainmentError, safe_join
from caseclerk_pipeline import queue

app = typer.Typer(help="CaseClerk: a case-files MCP server for a small law firm.", no_args_is_help=True)
app.add_typer(config_commands.app, name="config")


def _print_version(show: bool) -> None:
    if show:
        typer.echo(core_update.current_version())
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Print the installed version and exit.",
    ),
) -> None:
    """CaseClerk: a case-files MCP server for a small law firm."""


def _make_case_dir_resolver(conn: sqlite3.Connection, clio_root: Path) -> Callable[[int], Path]:
    def _resolve(case_id: int) -> Path:
        case = db.get_case(conn, case_id)
        if case is None:
            raise PathContainmentError(f"unknown case_id {case_id}")
        return safe_join(clio_root, case.rel_path)

    return _resolve


@app.command()
def serve() -> None:
    """Run the MCP server over stdio (what Claude Desktop/Code launches)."""
    # imported lazily so every other command's startup skips loading the mcp SDK
    from caseclerk_mcp.server import serve as mcp_serve

    mcp_serve()


@app.command()
def init(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept the top discovery candidate without prompting."
    ),
    write_claude_config: bool = typer.Option(
        False, "--write-claude-config", help="Write the Claude Desktop MCP server entry without prompting."
    ),
) -> None:
    """Discover the Clio Drive root, write config.json, and print MCP client setup lines."""
    candidates = discover()
    if not candidates:
        typer.echo(
            "No Clio Drive candidates found. Set it manually: caseclerk config set clioRoot <path>", err=True
        )
        raise typer.Exit(code=1)

    typer.echo("Candidates (best first):")
    for candidate in candidates:
        typer.echo(f"  {candidate.path}  (score {candidate.score})")

    chosen = candidates[0].path
    if not yes and not typer.confirm(f"Use '{chosen}' as clioRoot?", default=True):
        raise typer.Exit(code=1)

    cfg = load_config().model_copy(update={"clio_root": str(chosen)})
    saved_path = save_config(cfg)
    typer.echo(f"Wrote config to {saved_path}")

    typer.echo("\nFor Claude Code, run:\n  claude mcp add caseclerk -- caseclerk serve\n")
    typer.echo("For Claude Desktop, add this to claude_desktop_config.json:")
    typer.echo(json.dumps(SERVER_ENTRY, indent=2))

    should_write = write_claude_config
    if not should_write and not yes:
        should_write = typer.confirm(
            f"Write/merge this into {claude_desktop_config_path()} now?", default=False
        )
    if should_write:
        written_path = write_claude_desktop_entry()
        typer.echo(f"Updated Claude Desktop config at {written_path}")


@app.command()
def process(
    concurrency: int | None = typer.Option(
        None, "--concurrency", help="Override processing.concurrency for this run."
    ),
) -> None:
    """Scan clioRoot for new/changed documents and drain the processing queue once."""
    cfg = load_config()
    if not cfg.clio_root:
        typer.echo("clioRoot is not configured. Run `caseclerk init` first.", err=True)
        raise typer.Exit(code=1)
    root = Path(cfg.clio_root)

    conn = db.connect()
    try:
        result = scan.scan(
            conn, root, emails_folder_name=cfg.emails_folder_name, ignore_globs=cfg.processing.ignore
        )
        typer.echo(
            f"Scanned: {result.clients_seen} client(s), {result.cases_seen} case(s), "
            f"{result.documents_new} new, {result.documents_changed} changed, "
            f"{result.documents_unchanged} unchanged, {result.documents_removed} removed."
        )

        effective_concurrency = concurrency if concurrency is not None else cfg.processing.concurrency
        processed = queue.run_queue(
            conn, _make_case_dir_resolver(conn, root), concurrency=effective_concurrency
        )
        typer.echo(f"Processed {processed} job(s).")
    finally:
        conn.close()


@app.command()
def status() -> None:
    """Show processing queue/indexed/failed counts and any cached update-available version."""
    conn = db.connect()
    try:
        counts = db.status_counts(conn)
        cached_update = db.get_meta(conn, core_update.META_AVAILABLE_VERSION)
    finally:
        conn.close()

    typer.echo(f"Total documents: {counts.total}")
    for state, count in counts.by_state.items():
        typer.echo(f"  {state.value}: {count}")
    if cached_update:
        typer.echo(f"Update available: {cached_update} (run `caseclerk update`)")


@app.command()
def failures() -> None:
    """List every document currently in the failed state."""
    conn = db.connect()
    try:
        items = db.list_failures(conn)
    finally:
        conn.close()

    if not items:
        typer.echo("No failures.")
        return
    for failure in items:
        typer.echo(
            f"[{failure.document_id}] {failure.client_name}/{failure.case_number}/{failure.file_name}: "
            f"{failure.error} (attempts={failure.attempts})"
        )


@app.command()
def retry(
    document_id: int | None = typer.Argument(None, help="A specific document id to requeue."),
    all_failed: bool = typer.Option(False, "--all-failed", help="Requeue every currently-failed document."),
) -> None:
    """Requeue a document (by id) or every failed document with --all-failed."""
    if (document_id is None) == (not all_failed):
        typer.echo("Provide exactly one of: a document_id, or --all-failed.", err=True)
        raise typer.Exit(code=1)

    conn = db.connect()
    try:
        if all_failed:
            items = db.list_failures(conn)
            for failure in items:
                db.requeue_document(conn, failure.document_id)
            typer.echo(f"Requeued {len(items)} failed document(s).")
        else:
            assert document_id is not None
            document = db.get_document(conn, document_id)
            if document is None:
                typer.echo(f"No document {document_id}.", err=True)
                raise typer.Exit(code=1)
            db.requeue_document(conn, document_id)
            typer.echo(f"Requeued document {document_id}.")
    finally:
        conn.close()


@app.command()
def update() -> None:
    """Check GitHub Releases for a newer caseclerk-cli and, if found, apply it on the spot."""
    cfg = load_config()
    conn = db.connect()
    try:
        available = core_update.check_for_update(conn, check_interval_hours=cfg.updates.check_interval_hours)
    finally:
        conn.close()

    if available is None:
        typer.echo("No update available.")
        return

    typer.echo(f"Update available: {available}. Applying...")
    core_update.apply_update(available)
    typer.echo("Update started in the background. Restart caseclerk to use the new version.")


@app.command()
def doctor() -> None:
    """Check FTS5 availability, uv on PATH, config validity, clioRoot, and db writability."""
    healthy = True

    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE doctor_probe USING fts5(x)")
        probe.close()
        typer.echo("[ok]   sqlite3 has FTS5")
    except sqlite3.OperationalError:
        healthy = False
        typer.echo("[FAIL] sqlite3 lacks FTS5 -- use a python.org or uv-managed Python build")

    if shutil.which("uv"):
        typer.echo("[ok]   uv is on PATH")
    else:
        healthy = False
        typer.echo("[FAIL] uv is not on PATH (required for auto-update)")

    cfg = None
    try:
        cfg = load_config()
        typer.echo("[ok]   config is valid")
    except Exception as exc:  # noqa: BLE001 - doctor reports every problem, it doesn't crash on one
        healthy = False
        typer.echo(f"[FAIL] config is invalid: {exc}")

    if cfg is not None:
        if cfg.clio_root and Path(cfg.clio_root).is_dir():
            typer.echo(f"[ok]   clioRoot exists: {cfg.clio_root}")
        else:
            healthy = False
            typer.echo(f"[FAIL] clioRoot is not set or not a directory: {cfg.clio_root!r}")

    try:
        conn = db.connect()
        conn.execute("SELECT 1")
        conn.close()
        typer.echo("[ok]   database is writable")
    except Exception as exc:  # noqa: BLE001
        healthy = False
        typer.echo(f"[FAIL] database is not writable: {exc}")

    if not healthy:
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")
