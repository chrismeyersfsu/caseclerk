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

from caseclerk_cli import cloudflared as cloudflared_module
from caseclerk_cli import config_commands, share
from caseclerk_cli.claude_desktop import SERVER_ENTRY, claude_desktop_config_path, write_claude_desktop_entry
from caseclerk_core import binary_update, db, scan
from caseclerk_core import update as core_update
from caseclerk_core.config import load_config, save_config
from caseclerk_core.discovery import discover
from caseclerk_core.paths import PathContainmentError, safe_join
from caseclerk_pipeline import queue

app = typer.Typer(help="CaseClerk: a case-files MCP server for a small law firm.", no_args_is_help=True)
app.add_typer(config_commands.app, name="config")
app.add_typer(share.app, name="share")


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
    # Only a packaged (PyInstaller) install ever has .old-suffixed swap leftovers
    # to clean up (see caseclerk_core.binary_update); a no-op everywhere else.
    if binary_update.is_frozen():
        binary_update.cleanup_stale_files()


def _make_case_dir_resolver(conn: sqlite3.Connection, documents_root: Path) -> Callable[[int], Path]:
    def _resolve(case_id: int) -> Path:
        case = db.get_case(conn, case_id)
        if case is None:
            raise PathContainmentError(f"unknown case_id {case_id}")
        return safe_join(documents_root, case.rel_path)

    return _resolve


@app.command()
def serve(
    transport: str = typer.Option(
        "stdio", "--transport", help="stdio (default, what Claude Desktop/Code launches) or http."
    ),
    port: int | None = typer.Option(
        None, "--port", help="Port for --transport http (default: share.port from config)."
    ),
) -> None:
    """Run the MCP server. http is bound to 127.0.0.1 ONLY -- there is no --host flag;
    `caseclerk share start` (a cloudflared tunnel) is the only supported way to expose
    it beyond localhost."""
    if transport not in ("stdio", "http"):
        typer.echo(f"Unknown --transport {transport!r}; expected 'stdio' or 'http'.", err=True)
        raise typer.Exit(code=1)

    # imported lazily so every other command's startup skips loading the mcp SDK
    if transport == "stdio":
        from caseclerk_mcp.server import serve as mcp_serve

        mcp_serve()
    else:
        from caseclerk_mcp.server import serve_http as mcp_serve_http

        mcp_serve_http(port=port)


@app.command()
def init(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept the top discovery candidate without prompting."
    ),
    write_claude_config: bool = typer.Option(
        False, "--write-claude-config", help="Write the Claude Desktop MCP server entry without prompting."
    ),
) -> None:
    """Discover the documents root, write config.json, and print MCP client setup lines."""
    candidates = discover()
    if not candidates:
        typer.echo(
            "No documents-root candidates found. Set it manually: caseclerk config set documentsRoot <path>",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo("Candidates (best first):")
    for candidate in candidates:
        typer.echo(f"  {candidate.path}  (score {candidate.score})")

    chosen = candidates[0].path
    if not yes and not typer.confirm(f"Use '{chosen}' as documentsRoot?", default=True):
        raise typer.Exit(code=1)

    cfg = load_config().model_copy(update={"documents_root": str(chosen)})
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
    """Scan documentsRoot for new/changed documents and drain the processing queue once."""
    cfg = load_config()
    if not cfg.documents_root:
        typer.echo("documentsRoot is not configured. Run `caseclerk init` first.", err=True)
        raise typer.Exit(code=1)
    root = Path(cfg.documents_root)

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
    conn = db.connect()
    try:
        # interval 0: an explicit `caseclerk update` always asks GitHub fresh -- the
        # configured interval only paces the background auto-check, and honoring it
        # here made this command repeat a stale "no update" for up to a day.
        available = core_update.check_for_update(conn, check_interval_hours=0)
    finally:
        conn.close()

    if available is None:
        typer.echo("No update available.")
        return

    typer.echo(f"Update available: {available}. Applying...")
    result = core_update.apply_update(available)
    if isinstance(result, binary_update.BinaryUpdateResult):
        typer.echo(result.detail)
        if not result.ok:
            raise typer.Exit(code=1)
    else:
        typer.echo("Update started in the background. Restart caseclerk to use the new version.")


@app.command()
def audit(
    limit: int = typer.Option(20, "--limit", help="Maximum number of entries to show."),
) -> None:
    """Show the most recent HTTP-transport tool calls (stdio never writes these)."""
    conn = db.connect()
    try:
        entries = db.list_remote_requests(conn, limit=limit)
    finally:
        conn.close()

    if not entries:
        typer.echo("No audit entries yet.")
        return
    for entry in entries:
        label = "ok" if entry.ok else "FAIL"
        line = f"[{entry.ts.isoformat()}] {entry.tool} - {label}"
        if entry.error:
            line += f": {entry.error}"
        typer.echo(line)


@app.command()
def doctor() -> None:
    """Check FTS5 availability, uv on PATH (or, for a packaged binary, that it's
    packaged), config validity, documentsRoot, db writability, and (if `share`
    is configured) cloudflared's status."""
    healthy = True

    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE doctor_probe USING fts5(x)")
        probe.close()
        typer.echo("[ok]   sqlite3 has FTS5")
    except sqlite3.OperationalError:
        healthy = False
        typer.echo("[FAIL] sqlite3 lacks FTS5 -- use a python.org or uv-managed Python build")

    if binary_update.is_frozen():
        typer.echo("[ok]   running as a packaged binary (updates via GitHub Releases, not uv)")
    elif shutil.which("uv"):
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
        if cfg.documents_root and Path(cfg.documents_root).is_dir():
            typer.echo(f"[ok]   documentsRoot exists: {cfg.documents_root}")
        else:
            healthy = False
            typer.echo(f"[FAIL] documentsRoot is not set or not a directory: {cfg.documents_root!r}")

    try:
        conn = db.connect()
        conn.execute("SELECT 1")
        conn.close()
        typer.echo("[ok]   database is writable")
    except Exception as exc:  # noqa: BLE001
        healthy = False
        typer.echo(f"[FAIL] database is not writable: {exc}")

    if cfg is not None and cfg.share.hostname:
        cloudflared_bin = cloudflared_module.find_bundled() or cloudflared_module.find_cached()
        if cloudflared_bin is not None:
            source = cloudflared_module.source_label(cloudflared_bin)
            version = cloudflared_module.installed_version(cloudflared_bin) or "unknown version"
            typer.echo(f"[ok]   cloudflared ready ({source}, {version})")
        else:
            typer.echo(
                "[ok]   cloudflared not yet downloaded -- "
                "`caseclerk share start` (or `share setup`) fetches it automatically"
            )

    if not healthy:
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")
