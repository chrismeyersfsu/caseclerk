"""PyInstaller's entry point for the packaged `caseclerk` build.

Not the `caseclerk-cli` console-script wrapper hatchling generates at install
time (that's an installed-package artifact, not a source file PyInstaller can
point at directly) -- this is a plain script importing and invoking the same
typer app, which is what PyInstaller's static import analysis needs to find
every module the packaged build has to bundle.
"""

from caseclerk_cli.main import app

if __name__ == "__main__":
    app()
