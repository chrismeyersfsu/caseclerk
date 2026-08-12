"""Build a self-contained onedir bundle of the `caseclerk` CLI with PyInstaller,
and zip it as ``caseclerk-<os>-x64.zip`` -- the packaged install for a machine
with no Python/uv, per the "attorney's machine" distribution story.

Usage:
    uv run scripts/build_windows.py [--dist-dir dist-windows]

Named build_windows.py because Windows is the only platform this actually
ships for today (see caseclerk_core.binary_update.release_asset_name), but
the script runs on any OS: a Linux/macOS onedir build proves the PyInstaller
spec mechanics (entry point resolves, our own packages get bundled, the
result actually starts) without needing a Windows machine for every
iteration; the real, shipped artifact is built by the windows-latest job in
release.yml. Verify it actually runs with `--version`/`doctor` after
building, on whatever OS you built it on.

Several of the mcp SDK's own dependencies (opentelemetry-api, jsonschema,
starlette, uvicorn, sse-starlette in particular) use importlib.metadata
entry-point discovery or other import patterns PyInstaller's static analysis
doesn't always see through; COLLECT_ALL and COPY_METADATA below are
deliberately generous rather than hand-trimmed, since a missing one manifests
as a runtime ImportError/entry-point failure on the target machine, not a
build-time error here.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SCRIPT = REPO_ROOT / "scripts" / "_pyinstaller_entry.py"
APP_NAME = "caseclerk"

# Packages whose submodules, data files, and/or hidden imports PyInstaller's
# static analysis is prone to miss.
COLLECT_ALL = [
    "mcp",
    "mcp_types",
    "sse_starlette",
    "starlette",
    "uvicorn",
    "anyio",
    "httpx2",
    "jsonschema",
    "opentelemetry",
    "jwt",
    "multipart",
    "pydantic",
    "pydantic_core",
    "mammoth",
    "pdfminer",
    "typer",
    "click",
]

# Packages something in the dependency tree (our own --version, or a
# library's own self-check) resolves via importlib.metadata at runtime --
# metadata a frozen build doesn't have unless copied in explicitly.
COPY_METADATA = [
    "caseclerk-cli",
    "mcp",
    "opentelemetry-api",
    "jsonschema",
    "starlette",
    "uvicorn",
    "pydantic",
    "httpx",
]


def _os_arch_label() -> str:
    machine = "x64" if sys.maxsize > 2**32 else "x86"
    if sys.platform == "win32":
        return f"windows-{machine}"
    if sys.platform == "darwin":
        return f"macos-{machine}"
    return f"linux-{machine}"


def build(dist_dir: Path) -> Path:
    """Run PyInstaller and return the path to the built onedir bundle."""
    import PyInstaller.__main__

    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir = dist_dir / "build"
    spec_dir = dist_dir / "spec"

    args = [
        str(ENTRY_SCRIPT),
        "--name",
        APP_NAME,
        "--onedir",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
    ]
    for package in COLLECT_ALL:
        args += ["--collect-all", package]
    for package in COPY_METADATA:
        args += ["--copy-metadata", package]

    PyInstaller.__main__.run(args)
    return dist_dir / APP_NAME


def zip_bundle(bundle_dir: Path, dist_dir: Path) -> Path:
    label = _os_arch_label()
    archive_base = dist_dir / f"{APP_NAME}-{label}"
    archive_path = shutil.make_archive(
        str(archive_base), "zip", root_dir=bundle_dir.parent, base_dir=bundle_dir.name
    )
    return Path(archive_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=REPO_ROOT / "dist-windows",
        help="Output directory for the onedir bundle and the zip (default: ./dist-windows).",
    )
    args = parser.parse_args(argv)

    bundle_dir = build(args.dist_dir)
    exe_name = f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME
    exe_path = bundle_dir / exe_name
    if not exe_path.is_file():
        print(f"error: PyInstaller did not produce {exe_path}", file=sys.stderr)
        return 1

    archive_path = zip_bundle(bundle_dir, args.dist_dir)
    print(f"Built {bundle_dir}")
    print(f"Zipped {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
