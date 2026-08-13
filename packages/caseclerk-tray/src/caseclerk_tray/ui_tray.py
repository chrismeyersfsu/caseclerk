"""pystray + tkinter glue: builds the tray icon from the menu MODEL
(state.py), runs a background thread that polls state every ~5s and swaps
the icon/menu, and dispatches menu actions.

--- Threading arrangement (read this before touching anything below) -------

This pairing has a classic pitfall: tkinter's Tk instance MUST have its
mainloop driven from the thread that created it (true on every platform, not
just macOS), and pystray's `Icon.run()` is documented as needing the main
thread on backends where the OS itself demands it (macOS Cocoa). Windows'
Win32 backend has no such restriction -- its message loop can run on any
thread -- which is what makes the arrangement below possible for a
Windows-only app like this one:

  * The MAIN thread creates a single, hidden `tk.Tk()` root once at startup
    and calls `root.mainloop()` -- this becomes this process's de facto main
    loop for its entire life. All Status/Settings/Audit Toplevels are
    children of this one root.
  * pystray's icon loop runs on ITS OWN background thread via
    `icon.run_detached()` (safe here specifically because the Win32 backend
    doesn't require the main thread; this would need to be inverted on
    macOS). `run_detached()` returns immediately, so start-up order is:
    build icon -> run_detached() -> root.mainloop().
  * A THIRD thread (`_poll_loop` below) periodically rebuilds TrayState and
    pushes new `icon.icon`/`icon.menu` -- pystray's own setters are safe to
    call from any thread; they marshal onto its loop thread internally.
  * The one rule every menu/poll callback follows: tkinter is not
    thread-safe, so nothing outside the main thread may create or touch a Tk
    widget directly. Any action that needs a window (Status/Settings/Audit,
    or the quit confirmation dialog) is scheduled via `root.after(0, ...)`
    from wherever the pystray/poll thread's callback runs, which marshals
    the call onto the main thread's event queue instead of running it
    in-place.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from caseclerk_core import binary_update, db
from caseclerk_core.config import load_config
from caseclerk_tray import actions, icon, state, windows
from caseclerk_tray.state import TrayState

if TYPE_CHECKING:
    import tkinter as tk

    import pystray

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0


class _TrayApp:
    def __init__(self) -> None:
        import tkinter as tk

        self.root: tk.Tk = tk.Tk()
        # Never shown on its own -- this root exists only to drive mainloop() and parent the Toplevels.
        self.root.withdraw()

        self._icon: pystray.Icon | None = None
        self._stop_event = threading.Event()
        self._current_state = self._collect_state()

    # --- state -----------------------------------------------------------

    def _collect_state(self) -> TrayState:
        cfg = load_config()
        conn = db.connect()
        try:
            autostart_enabled = actions.autostart.is_enabled()
            return state.collect_state(conn, cfg, autostart_enabled=autostart_enabled)
        finally:
            conn.close()

    def get_state(self) -> TrayState:
        return self._current_state

    # --- icon/menu construction -------------------------------------------

    def _build_icon(self) -> pystray.Icon:
        import pystray

        image = icon.sharing_on_icon() if self._current_state.sharing_on else icon.sharing_off_icon()
        return pystray.Icon("caseclerk-tray", image, "CaseClerk", menu=self._build_menu())

    def _build_menu(self) -> pystray.Menu:
        import pystray

        model = state.build_menu_model(self._current_state)
        entries: list[Any] = []
        for item in model:
            if item.separator:
                entries.append(pystray.Menu.SEPARATOR)
                continue
            # Only is_checkbox items (currently just "start on login") pass a
            # `checked` callback -- pystray renders ANY item with a non-None
            # `checked` as a checkbox, so plain action items must pass None.
            # The callback pystray calls takes the MenuItem itself as its one
            # argument; `value=item.checked` binds this loop iteration's flag
            # by default-argument, the standard fix for a closure-in-a-loop.
            checked = (lambda _menu_item, value=item.checked: value) if item.is_checkbox else None
            entries.append(
                pystray.MenuItem(
                    item.label,
                    self._make_handler(item.action_id),
                    checked=checked,
                    enabled=item.enabled,
                )
            )
        return pystray.Menu(*entries)

    def _make_handler(self, action_id: str) -> Any:
        def _handler(icon_obj: pystray.Icon, item: object) -> None:
            self._dispatch(action_id)

        return _handler

    def _dispatch(self, action_id: str) -> None:
        # Runs on pystray's background thread -- only touch tkinter via
        # root.after(...); everything else here is plain Python/IO.
        if action_id == state.ACTION_TOGGLE_SHARING:
            if self._current_state.sharing_on:
                actions.stop_sharing()
            else:
                actions.start_sharing()
            self.refresh()
        elif action_id == state.ACTION_OPEN_STATUS:
            self.root.after(0, self._open_status)
        elif action_id == state.ACTION_OPEN_SETTINGS:
            self.root.after(0, self._open_settings)
        elif action_id == state.ACTION_OPEN_AUDIT:
            self.root.after(0, self._open_audit)
        elif action_id == state.ACTION_TOGGLE_AUTOSTART:
            actions.set_autostart(not bool(self._current_state.autostart_enabled))
            self.refresh()
        elif action_id == state.ACTION_CHECK_UPDATES:
            self._check_updates()
        elif action_id == state.ACTION_RESTART_TO_UPDATE:
            self._restart_to_update()
        elif action_id == state.ACTION_QUIT:
            self.root.after(0, self._handle_quit)

    # --- window helpers (main-thread only) --------------------------------

    def _open_status(self) -> None:
        windows.open_status_window(
            self.root,
            get_state=self.get_state,
            on_toggle_sharing=lambda: self._dispatch(state.ACTION_TOGGLE_SHARING),
            on_check_updates=self._check_updates,
        )

    def _open_settings(self) -> None:
        windows.open_settings_window(self.root)

    def _open_audit(self) -> None:
        windows.open_audit_window(self.root)

    def _handle_quit(self) -> None:
        if self._current_state.sharing_on:
            answer = windows.confirm_quit_while_sharing()
            if answer is None:
                return  # Cancel: do nothing
            if answer:
                actions.stop_sharing()
        self._shutdown()

    # --- update handling ---------------------------------------------------

    def _check_updates(self) -> None:
        cfg = load_config()
        conn = db.connect()
        try:
            available = actions.check_for_update(conn, cfg)
        finally:
            conn.close()

        if available and binary_update.is_frozen():
            result = actions.apply_staged_update(available)
            if result is not None and not result.ok:
                logger.warning("update to %s failed: %s", available, result.detail)
        self.refresh()

    def _restart_to_update(self) -> None:
        actions.relaunch()
        self.root.after(0, self._shutdown)

    # --- lifecycle ----------------------------------------------------------

    def refresh(self) -> None:
        self._current_state = self._collect_state()
        if self._icon is not None:
            self._icon.icon = (
                icon.sharing_on_icon() if self._current_state.sharing_on else icon.sharing_off_icon()
            )
            self._icon.menu = self._build_menu()

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(POLL_INTERVAL_SECONDS):
            try:
                self.refresh()
            except Exception:
                logger.exception("tray poll loop failed; will retry on the next interval")

    def _shutdown(self) -> None:
        self._stop_event.set()
        if self._icon is not None:
            self._icon.stop()
        self.root.quit()

    def run(self) -> None:
        self._icon = self._build_icon()
        self._icon.run_detached()  # background thread -- see module docstring

        poll_thread = threading.Thread(target=self._poll_loop, name="caseclerk-tray-poll", daemon=True)
        poll_thread.start()

        try:
            self.root.mainloop()  # main thread -- this call blocks until _shutdown() calls root.quit()
        finally:
            self._stop_event.set()
            if self._icon is not None:
                self._icon.stop()


def run() -> None:
    _TrayApp().run()
