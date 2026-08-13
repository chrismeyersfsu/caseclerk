"""PyInstaller's entry point for the packaged `caseclerk-tray` build. See
_pyinstaller_entry.py's docstring for why this exists (a plain importable
script PyInstaller's static analysis can point at, not the hatchling-
generated console-script wrapper hatchling would otherwise install).
"""

from caseclerk_tray.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
