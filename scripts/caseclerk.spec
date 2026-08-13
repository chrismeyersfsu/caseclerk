# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building BOTH caseclerk.exe (console CLI) and
caseclerk-tray.exe (windowed tray app) into a single onedir bundle sharing
one _internal directory -- PyInstaller's documented "multipackage bundle"
pattern: two Analysis objects, MERGE()'d so modules both entry points need
are only stored once, one COLLECT holding both EXEs. See
scripts/build_windows.py's module docstring for why this needs an actual
.spec file rather than plain `--onedir` CLI args: a single PyInstaller
invocation without one cannot produce two independent executables that share
one support directory.

COLLECT_ALL/COPY_METADATA/ENTRY_SCRIPT/APP_NAME (and their TRAY_* siblings)
are imported from build_windows.py -- its own directory (this file's
directory, SPECPATH) is put on sys.path automatically when PyInstaller execs
a .spec file -- so the package lists stay defined in exactly one place.

`Analysis`/`MERGE`/`PYZ`/`EXE`/`COLLECT` are injected into this file's
globals by PyInstaller's own spec-exec machinery; they are not local/stdlib
names, which is why nothing here explicitly imports them.
"""

from PyInstaller.utils.hooks import collect_all, copy_metadata

from build_windows import (
    APP_NAME,
    COLLECT_ALL,
    COPY_METADATA,
    ENTRY_SCRIPT,
    TRAY_APP_NAME,
    TRAY_COLLECT_ALL,
    TRAY_COPY_METADATA,
    TRAY_ENTRY_SCRIPT,
)


def _collected(packages):
    datas, binaries, hiddenimports = [], [], []
    for package in packages:
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
    return datas, binaries, hiddenimports


def _metadata(packages):
    datas = []
    for package in packages:
        datas += copy_metadata(package)
    return datas


cli_datas, cli_binaries, cli_hidden = _collected(COLLECT_ALL)
cli_datas += _metadata(COPY_METADATA)

tray_datas, tray_binaries, tray_hidden = _collected(TRAY_COLLECT_ALL)
tray_datas += _metadata(TRAY_COPY_METADATA)

a_cli = Analysis(
    [str(ENTRY_SCRIPT)],
    datas=cli_datas,
    binaries=cli_binaries,
    hiddenimports=cli_hidden,
)

a_tray = Analysis(
    [str(TRAY_ENTRY_SCRIPT)],
    datas=tray_datas,
    binaries=tray_binaries,
    hiddenimports=tray_hidden,
)

MERGE((a_cli, APP_NAME, APP_NAME), (a_tray, TRAY_APP_NAME, TRAY_APP_NAME))

pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=True,
)

pyz_tray = PYZ(a_tray.pure)
exe_tray = EXE(
    pyz_tray,
    a_tray.scripts,
    [],
    exclude_binaries=True,
    name=TRAY_APP_NAME,
    # No console window: the tray is a background/notification-area app.
    # See caseclerk_tray.__main__._attach_console_for_smoke for how
    # `caseclerk-tray.exe --smoke` still makes its status line visible to a
    # CI/terminal caller despite this.
    console=False,
)

COLLECT(
    exe_cli,
    a_cli.binaries,
    a_cli.zipfiles,
    a_cli.datas,
    exe_tray,
    a_tray.binaries,
    a_tray.zipfiles,
    a_tray.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
