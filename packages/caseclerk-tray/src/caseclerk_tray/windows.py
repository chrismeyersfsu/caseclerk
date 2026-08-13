"""tkinter/ttk windows: Status, Settings (incl. remote-sharing setup), Audit,
and the quit confirmation dialog.

Threading: every function below that touches tkinter must only ever run on
the thread driving `root.mainloop()` -- see ui_tray.py's module docstring for
the full pystray+tkinter arrangement. A pystray menu callback (which runs on
pystray's own background thread) opens one of these windows by scheduling it
via `root.after(0, ...)`, never by calling it directly.

`tkinter` itself is imported lazily, inside each function that needs it
(never at module scope), so that this module can be imported -- and its pure
helpers (`validate_settings`, `SettingsFormValues`) unit-tested -- on any
platform/CI runner, including a headless one with no display, without ever
requiring a live Tk/display stack.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from caseclerk_core.config import Config, load_config, save_config
from caseclerk_tray import actions
from caseclerk_tray.state import TrayState

if TYPE_CHECKING:
    import tkinter as tk

# --- Settings: pure validation, no tkinter ----------------------------------


@dataclass(frozen=True)
class SettingsFormValues:
    """Raw (still-string) values read off the Settings window's widgets."""

    documents_root: str
    emails_folder_name: str
    email_file_name_template: str
    processing_concurrency: str
    autostart_enabled: bool


def validate_settings(values: SettingsFormValues) -> tuple[Config | None, list[str]]:
    """Parse+validate the Settings form into a `Config` update, or a list of
    human-readable errors if anything is invalid. No tkinter involved -- this
    is what the Save button's handler calls, and what tests exercise
    directly. Returns `(None, errors)` on failure, `(config, [])` on success.
    """
    errors: list[str] = []

    documents_root = values.documents_root.strip()
    if not documents_root:
        errors.append("Documents folder is required.")
    elif not Path(documents_root).is_dir():
        errors.append(f"Documents folder does not exist: {documents_root}")

    emails_folder_name = values.emails_folder_name.strip()
    if not emails_folder_name:
        errors.append("Emails folder name is required.")

    email_file_name_template = values.email_file_name_template.strip()
    if not email_file_name_template:
        errors.append("Email file name template is required.")

    concurrency = 1
    try:
        concurrency = int(values.processing_concurrency.strip())
        if concurrency < 1:
            errors.append("Processing concurrency must be at least 1.")
    except ValueError:
        errors.append("Processing concurrency must be a whole number.")

    if errors:
        return None, errors

    cfg = load_config()
    updated = cfg.model_copy(
        update={
            "documents_root": documents_root,
            "emails_folder_name": emails_folder_name,
            "email_file_name_template": email_file_name_template,
            "processing": cfg.processing.model_copy(update={"concurrency": concurrency}),
        }
    )
    return updated, []


def save_settings(values: SettingsFormValues) -> list[str]:
    """Validate and, if valid, persist config + the autostart checkbox.
    Returns the validation errors (empty list on success)."""
    updated, errors = validate_settings(values)
    if updated is None:
        return errors
    save_config(updated)
    actions.set_autostart(values.autostart_enabled)
    return []


# --- Quit confirmation -------------------------------------------------------


def confirm_quit_while_sharing() -> bool | None:
    """Sharing is ON: ask whether to stop it first. True = stop then quit,
    False = quit but leave sharing running, None = cancel (do nothing) --
    this mirrors `tkinter.messagebox.askyesnocancel`'s own Yes/No/Cancel
    return convention exactly, so callers don't need a translation layer."""
    from tkinter import messagebox

    return messagebox.askyesnocancel(
        "Quit CaseClerk",
        "Sharing is currently ON. Stop sharing before quitting?\n\n"
        "Yes: stop sharing and quit\n"
        "No: quit, but leave sharing running\n"
        "Cancel: don't quit",
    )


# --- Status window ------------------------------------------------------


def open_status_window(
    root: tk.Tk,
    *,
    get_state: Callable[[], TrayState],
    on_toggle_sharing: Callable[[], None],
    on_check_updates: Callable[[], None],
) -> None:
    import tkinter as tk
    from tkinter import ttk

    from caseclerk_core import db

    state = get_state()

    win = tk.Toplevel(root)
    win.title("CaseClerk Status")
    win.geometry("420x420")
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=12)
    frame.pack(fill="both", expand=True)

    sharing_var = tk.StringVar(value=f"Sharing: {'ON' if state.sharing_on else 'OFF'}")
    ttk.Label(frame, textvariable=sharing_var, font=("", 11, "bold")).pack(anchor="w")

    def _toggle() -> None:
        on_toggle_sharing()
        refresh()

    toggle_button = ttk.Button(
        frame, text="Stop Sharing" if state.sharing_on else "Start Sharing", command=_toggle
    )
    toggle_button.pack(anchor="w", pady=(4, 12))

    counts_var = tk.StringVar()
    ttk.Label(frame, textvariable=counts_var, justify="left").pack(anchor="w")

    ttk.Label(frame, text="Failures:").pack(anchor="w", pady=(12, 2))
    failures_tree = ttk.Treeview(frame, columns=("file", "case", "error"), show="headings", height=8)
    for col, heading, width in (("file", "File", 140), ("case", "Case", 100), ("error", "Error", 160)):
        failures_tree.heading(col, text=heading)
        failures_tree.column(col, width=width)
    failures_tree.pack(fill="both", expand=True, pady=(0, 12))

    version_var = tk.StringVar()
    ttk.Label(frame, textvariable=version_var).pack(anchor="w")

    def _check_updates() -> None:
        on_check_updates()
        refresh()

    ttk.Button(frame, text="Check for updates", command=_check_updates).pack(anchor="w", pady=(6, 0))

    def refresh() -> None:
        current = get_state()
        sharing_var.set(f"Sharing: {'ON' if current.sharing_on else 'OFF'}")
        toggle_button.configure(text="Stop Sharing" if current.sharing_on else "Start Sharing")
        by_state_lines = "\n".join(f"  {name}: {n}" for name, n in current.processing_by_state.items())
        counts_var.set(
            f"Processing total: {current.processing_total}\nFailures: {current.failure_count}\n"
            f"{by_state_lines}"
        )

        for row in failures_tree.get_children():
            failures_tree.delete(row)
        conn = db.connect()
        try:
            for failure in db.list_failures(conn):
                failures_tree.insert(
                    "", "end", values=(failure.file_name, failure.case_number, failure.error)
                )
        finally:
            conn.close()

        version_line = f"Version: {current.current_version}"
        if current.update_available_version:
            version_line += f" (update available: {current.update_available_version})"
        version_var.set(version_line)

    refresh()


# --- Settings window ------------------------------------------------------


def open_settings_window(root: tk.Tk) -> None:
    import tkinter as tk
    from tkinter import filedialog, ttk

    cfg = load_config()

    win = tk.Toplevel(root)
    win.title("CaseClerk Settings")
    win.geometry("480x560")
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=12)
    frame.pack(fill="both", expand=True)

    documents_root_var = tk.StringVar(value=cfg.documents_root or "")
    emails_folder_var = tk.StringVar(value=cfg.emails_folder_name)
    email_template_var = tk.StringVar(value=cfg.email_file_name_template)
    concurrency_var = tk.StringVar(value=str(cfg.processing.concurrency))
    autostart_var = tk.BooleanVar(value=bool(actions.autostart.is_enabled()))
    error_var = tk.StringVar(value="")

    ttk.Label(frame, text="Documents folder:").grid(row=0, column=0, sticky="w")
    ttk.Entry(frame, textvariable=documents_root_var, width=38).grid(row=1, column=0, sticky="we")

    def _browse_documents_root() -> None:
        chosen = filedialog.askdirectory(parent=win, initialdir=documents_root_var.get() or None)
        if chosen:
            documents_root_var.set(chosen)

    ttk.Button(frame, text="Browse...", command=_browse_documents_root).grid(row=1, column=1, padx=(6, 0))

    ttk.Label(frame, text="Emails folder name:").grid(row=2, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=emails_folder_var, width=38).grid(row=3, column=0, sticky="we")

    ttk.Label(frame, text="Email file name template:").grid(row=4, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=email_template_var, width=38).grid(row=5, column=0, sticky="we")

    ttk.Label(frame, text="Processing concurrency:").grid(row=6, column=0, sticky="w", pady=(10, 0))
    ttk.Spinbox(frame, from_=1, to=32, textvariable=concurrency_var, width=6).grid(
        row=7, column=0, sticky="w"
    )

    share_hostname_var = tk.StringVar(value=cfg.share.hostname or "(not configured)")
    ttk.Label(frame, text="Share hostname (read-only):").grid(row=8, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=share_hostname_var, width=38, state="readonly").grid(
        row=9, column=0, sticky="we"
    )

    share_port_var = tk.StringVar(value=str(cfg.share.port))
    ttk.Label(frame, text="Share port (read-only):").grid(row=10, column=0, sticky="w", pady=(10, 0))
    ttk.Entry(frame, textvariable=share_port_var, width=10, state="readonly").grid(
        row=11, column=0, sticky="w"
    )

    ttk.Checkbutton(frame, text="Start CaseClerk when Windows starts", variable=autostart_var).grid(
        row=12, column=0, columnspan=2, sticky="w", pady=(12, 0)
    )

    ttk.Separator(frame).grid(row=13, column=0, columnspan=2, sticky="we", pady=12)

    _build_remote_sharing_section(win, frame, start_row=14, cfg=cfg)

    error_label = ttk.Label(frame, textvariable=error_var, foreground="red", wraplength=440, justify="left")
    error_label.grid(row=22, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _save() -> None:
        values = SettingsFormValues(
            documents_root=documents_root_var.get(),
            emails_folder_name=emails_folder_var.get(),
            email_file_name_template=email_template_var.get(),
            processing_concurrency=concurrency_var.get(),
            autostart_enabled=autostart_var.get(),
        )
        errors = save_settings(values)
        if errors:
            error_label.configure(foreground="red")
            error_var.set("\n".join(errors))
        else:
            error_label.configure(foreground="green")
            error_var.set("Saved.")

    ttk.Button(frame, text="Save", command=_save).grid(row=23, column=0, sticky="w", pady=(12, 0))


def _build_remote_sharing_section(win: tk.Toplevel, frame: Any, *, start_row: int, cfg: Config) -> None:
    """The "Remote sharing setup" section: credentials file + hostname +
    a button that runs the exact same `share.setup_credentials` flow as
    `caseclerk share setup --credentials ... --hostname ...` (via
    `actions.setup_sharing`) -- one implementation, two front ends."""
    import tkinter as tk
    from tkinter import filedialog, ttk

    ttk.Label(frame, text="Remote sharing setup", font=("", 10, "bold")).grid(
        row=start_row, column=0, columnspan=2, sticky="w"
    )

    already_configured = bool(cfg.share.hostname)
    current_label_var = tk.StringVar(
        value=f"Currently configured: {cfg.share.hostname}" if already_configured else "Not yet configured."
    )
    ttk.Label(frame, textvariable=current_label_var).grid(
        row=start_row + 1, column=0, columnspan=2, sticky="w"
    )

    credentials_var = tk.StringVar(value="")
    ttk.Label(frame, text="Tunnel credentials JSON:").grid(
        row=start_row + 2, column=0, sticky="w", pady=(8, 0)
    )
    ttk.Entry(frame, textvariable=credentials_var, width=38, state="readonly").grid(
        row=start_row + 3, column=0, sticky="we"
    )

    def _browse_credentials() -> None:
        chosen = filedialog.askopenfilename(
            parent=win, filetypes=[("Tunnel credentials JSON", "*.json"), ("All files", "*.*")]
        )
        if chosen:
            credentials_var.set(chosen)

    ttk.Button(frame, text="Browse...", command=_browse_credentials).grid(
        row=start_row + 3, column=1, padx=(6, 0)
    )

    hostname_var = tk.StringVar(value=cfg.share.hostname or "")
    ttk.Label(frame, text="Hostname (e.g. caseclerk.example.com):").grid(
        row=start_row + 4, column=0, sticky="w", pady=(8, 0)
    )
    ttk.Entry(frame, textvariable=hostname_var, width=38).grid(row=start_row + 5, column=0, sticky="we")

    setup_status_var = tk.StringVar(value="")
    setup_status_label = ttk.Label(frame, textvariable=setup_status_var, wraplength=440, justify="left")
    setup_status_label.grid(row=start_row + 7, column=0, columnspan=2, sticky="w", pady=(6, 0))

    setup_button = ttk.Button(frame, text="Reconfigure..." if already_configured else "Set up sharing")

    def _run_setup() -> None:
        credentials_raw = credentials_var.get().strip()
        if not credentials_raw:
            setup_status_label.configure(foreground="red")
            setup_status_var.set("Choose a tunnel credentials JSON file first.")
            return
        outcome = actions.setup_sharing(Path(credentials_raw), hostname=hostname_var.get(), tunnel_name=None)
        if outcome.ok:
            setup_status_label.configure(foreground="green")
            setup_status_var.set(f"{outcome.message}\nPublic URL: {outcome.public_url}")
            setup_button.configure(text="Reconfigure...")
            current_label_var.set(f"Currently configured: {outcome.hostname}")
        else:
            setup_status_label.configure(foreground="red")
            setup_status_var.set(outcome.message)

    setup_button.configure(command=_run_setup)
    setup_button.grid(row=start_row + 6, column=0, sticky="w", pady=(8, 0))


# --- Audit window -------------------------------------------------------


def open_audit_window(root: tk.Tk, *, limit: int = 50) -> None:
    import tkinter as tk
    from tkinter import ttk

    from caseclerk_core import db

    win = tk.Toplevel(root)
    win.title("CaseClerk Audit Log")
    win.geometry("560x400")

    frame = ttk.Frame(win, padding=12)
    frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(frame, columns=("ts", "tool", "ok", "error"), show="headings")
    for col, heading, width in (
        ("ts", "Time", 160),
        ("tool", "Tool", 140),
        ("ok", "Result", 60),
        ("error", "Error", 160),
    ):
        tree.heading(col, text=heading)
        tree.column(col, width=width)
    tree.pack(fill="both", expand=True)

    conn = db.connect()
    try:
        entries = db.list_remote_requests(conn, limit=limit)
    finally:
        conn.close()

    if not entries:
        ttk.Label(frame, text="No audit entries yet.").pack(anchor="w", pady=(8, 0))
        return

    for entry in entries:
        tree.insert(
            "",
            "end",
            values=(entry.ts.isoformat(), entry.tool, "ok" if entry.ok else "FAIL", entry.error or ""),
        )
