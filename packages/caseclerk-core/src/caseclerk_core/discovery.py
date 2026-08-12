"""Documents root auto-discovery.

Every function here is pure and takes its OS/filesystem inputs as
arguments (or accepts an injected candidate list outright), so tests
never touch the real filesystem beyond a temp directory they built.
"""

from __future__ import annotations

import contextlib
import platform
import re
import string
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_CASE_NUMBER_CHARS = re.compile(r"^[A-Za-z0-9-]+$")


def _safe_is_dir(path: Path) -> bool:
    with contextlib.suppress(OSError):
        return path.is_dir()
    return False


def looks_like_case_number(name: str) -> bool:
    """Alnum-with-dashes containing at least one digit, e.g. '2026-0142'."""
    return bool(_CASE_NUMBER_CHARS.match(name)) and any(ch.isdigit() for ch in name)


def score_candidate(root: Path) -> int:
    """Count top-level dirs shaped like a client folder: contains >=1 case-number-shaped subdir.

    Scans of real mount points (``/mnt``, ``/media``, ``/Volumes``) routinely hit entries
    the current user can't stat (permission-denied system files, broken mounts); every
    filesystem call here is per-entry try/except'd so one bad entry just scores as "not a
    match" instead of aborting discovery for every other candidate.
    """
    try:
        if not root.is_dir():
            return 0
        entries = list(root.iterdir())
    except OSError:
        return 0

    score = 0
    for entry in entries:
        try:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            sub_entries = list(entry.iterdir())
        except OSError:
            continue
        try:
            if any(sub.is_dir() and looks_like_case_number(sub.name) for sub in sub_entries):
                score += 1
        except OSError:
            continue
    return score


def candidate_paths(system: str | None = None, home: Path | None = None) -> list[Path]:
    """Per-OS list of directories worth scoring as a possible documents root.

    No name-based hints (no specific product/vendor folder names) -- just generic
    home-folder locations plus every per-OS place an external drive or mount could
    show up. score_candidate() is what actually decides whether a candidate looks
    like a <client>/<case-number> tree; this just casts a wide, unopinionated net.
    """
    system = system or platform.system()
    home = home or Path.home()
    candidates: list[Path] = []

    if system == "Windows":
        candidates.extend(Path(f"{letter}:\\") for letter in string.ascii_uppercase)
        candidates.append(home / "Documents")
        candidates.append(home / "Cases")
    elif system == "Darwin":
        volumes = Path("/Volumes")
        if volumes.is_dir():
            with contextlib.suppress(OSError):
                for entry in volumes.iterdir():
                    if _safe_is_dir(entry):
                        candidates.append(entry)
        candidates.append(home / "Documents")
        candidates.append(home / "Cases")
    else:
        candidates.append(home / "Documents")
        candidates.append(home / "Cases")
        for base in (Path("/mnt"), Path("/media")):
            if base.is_dir():
                with contextlib.suppress(OSError):
                    for entry in base.iterdir():
                        if _safe_is_dir(entry):
                            candidates.append(entry)

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


@dataclass(frozen=True)
class DiscoveryCandidate:
    path: Path
    score: int


def discover(
    system: str | None = None,
    home: Path | None = None,
    roots: Iterable[Path] | None = None,
) -> list[DiscoveryCandidate]:
    """Score candidates (real OS probing, or an injected ``roots`` list) best-first."""
    paths = list(roots) if roots is not None else candidate_paths(system=system, home=home)
    scored = [DiscoveryCandidate(path=p, score=score_candidate(p)) for p in paths]
    positive = [c for c in scored if c.score > 0]
    positive.sort(key=lambda c: c.score, reverse=True)
    return positive


def best_candidate(
    system: str | None = None,
    home: Path | None = None,
    roots: Iterable[Path] | None = None,
) -> Path | None:
    ranked = discover(system=system, home=home, roots=roots)
    return ranked[0].path if ranked else None
