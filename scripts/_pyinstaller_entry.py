"""PyInstaller's entry point for the packaged `caseclerk` build.

Not the `caseclerk-cli` console-script wrapper hatchling generates at install
time (that's an installed-package artifact, not a source file PyInstaller can
point at directly) -- this is a plain script importing and invoking the same
typer app, which is what PyInstaller's static import analysis needs to find
every module the packaged build has to bundle (PyInstaller's Analysis walks
the whole AST for imports, including ones nested inside `if __name__`, so
importing here instead of at module level costs nothing for that purpose).
"""

import multiprocessing

if __name__ == "__main__":
    # Must run before anything else in this block: multiprocessing's spawn
    # start method (the only one available on Windows) re-executes this very
    # frozen exe to bootstrap each worker process. Without this call, that
    # re-exec runs straight into the normal app below instead of
    # multiprocessing's own internal bootstrap -- which is exactly what
    # crashed `caseclerk process` (concurrency >= 2, the default) with
    # BrokenProcessPool + "[PYI-3812] Failed to execute script
    # '_pyinstaller_entry'" the moment ProcessPoolExecutor tried to spawn a
    # worker; `--concurrency 1` (the inline, no-pool path) was unaffected.
    # See PyInstaller's own multiprocessing documentation.
    multiprocessing.freeze_support()

    from caseclerk_cli.main import app

    app()
