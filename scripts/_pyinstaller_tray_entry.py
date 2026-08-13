"""PyInstaller's entry point for the packaged `caseclerk-tray` build. See
_pyinstaller_entry.py's docstring for why this exists (a plain importable
script PyInstaller's static analysis can point at, not the hatchling-
generated console-script wrapper hatchling would otherwise install).
"""

import multiprocessing

if __name__ == "__main__":
    # Required for the same reason as _pyinstaller_entry.py's matching call
    # (see its comment): every frozen Windows exe needs this before running
    # its normal app, not just the one that directly uses a process pool --
    # caseclerk-tray.exe doesn't spawn one itself today, but it does share
    # this exact bootstrap mechanism, and a no-op call here is free.
    multiprocessing.freeze_support()

    from caseclerk_tray.__main__ import main

    raise SystemExit(main())
