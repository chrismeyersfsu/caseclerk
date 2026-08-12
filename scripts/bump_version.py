"""Lockstep semver bump across every packages/*/pyproject.toml.

Usage:
    uv run scripts/bump_version.py <major|minor|patch|X.Y.Z> [--dry-run]

Reads every package's current `[project].version` (they must already
agree -- an invariant this script itself maintains), computes the new
version, rewrites it into every packages/*/pyproject.toml, and runs
`uv lock` so uv.lock stays in sync. Prints the resulting version and a
reminder to tag it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = REPO_ROOT / "packages"
_VERSION_LINE_RE = re.compile(r'(?m)^(version\s*=\s*)"([^"]+)"')
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BUMP_KINDS = ("major", "minor", "patch")


class VersionError(Exception):
    """A version-bump precondition failed (out-of-lockstep versions, bad input, ...)."""


def _package_pyprojects() -> list[Path]:
    paths = sorted(PACKAGES_DIR.glob("*/pyproject.toml"))
    if not paths:
        raise VersionError(f"no packages found under {PACKAGES_DIR}")
    return paths


def _read_version(pyproject: Path) -> str:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    try:
        return str(data["project"]["version"])
    except KeyError as exc:
        raise VersionError(f"{pyproject} has no [project].version") from exc


def current_version() -> str:
    """The workspace's lockstep version. Raises if any package has drifted out of sync."""
    versions = {p: _read_version(p) for p in _package_pyprojects()}
    distinct = set(versions.values())
    if len(distinct) != 1:
        detail = "\n".join(f"  {p.parent.name}: {v}" for p, v in sorted(versions.items()))
        raise VersionError(f"package versions are out of lockstep:\n{detail}")
    return distinct.pop()


def parse_semver(text: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(text.strip())
    if not match:
        raise VersionError(f"not a plain X.Y.Z semver string: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def bump(current: str, target: str) -> str:
    """target is 'major' | 'minor' | 'patch', or an explicit X.Y.Z string."""
    if target in _BUMP_KINDS:
        major, minor, patch = parse_semver(current)
        if target == "major":
            return f"{major + 1}.0.0"
        if target == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"
    parse_semver(target)  # validate shape; raises VersionError if malformed
    return target


def _rewrite_version(pyproject: Path, new_version: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    new_text, count = _VERSION_LINE_RE.subn(lambda m: f'{m.group(1)}"{new_version}"', text, count=1)
    if count != 1:
        raise VersionError(f'could not find a version = "..." line in {pyproject}')
    pyproject.write_text(new_text, encoding="utf-8")


def _run_uv_lock() -> None:
    subprocess.run(["uv", "lock"], cwd=REPO_ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="major | minor | patch | an explicit X.Y.Z version")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the resulting version without writing anything"
    )
    args = parser.parse_args(argv)

    try:
        current = current_version()
        new_version = bump(current, args.target)
    except VersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"{current} -> {new_version} (dry run; nothing written)")
        return 0

    for pyproject in _package_pyprojects():
        _rewrite_version(pyproject, new_version)
    _run_uv_lock()

    print(new_version)
    print(f"Wrote {new_version} to every packages/*/pyproject.toml and ran `uv lock`.")
    print(
        f"Next: git add -A && git commit -m 'Bump version to {new_version}' "
        f"&& git tag v{new_version} && git push origin main v{new_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
