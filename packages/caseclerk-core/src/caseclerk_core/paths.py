"""Filesystem containment guardrails.

Every module that touches files under ``documentsRoot`` goes through
:func:`safe_join` (directly, or via :func:`case_dir`). It is the only
place traversal (``..``) and symlink escapes are rejected, by resolving
both the root and the candidate to their real, symlink-free paths and
checking the candidate is still rooted under the real root.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath


class PathContainmentError(Exception):
    """A candidate path would resolve outside its allowed root."""


def safe_join(root: str | os.PathLike[str], *parts: str) -> Path:
    """Join ``parts`` onto ``root`` and guarantee the result stays under it.

    Neither ``root`` nor the final path need to exist yet (callers may be
    computing a path to create). Any part that is itself absolute, or any
    combination of parts/symlinks that resolves outside ``root``, raises
    :class:`PathContainmentError`.
    """
    root_real = Path(os.path.realpath(root))
    candidate = root_real
    for part in parts:
        if not part:
            continue
        piece = PurePath(part)
        if piece.is_absolute() or piece.drive:
            raise PathContainmentError(f"absolute path segment not allowed: {part!r}")
        candidate = candidate / piece

    candidate_real = Path(os.path.realpath(candidate))
    try:
        common = os.path.commonpath([str(root_real), str(candidate_real)])
    except ValueError as exc:
        raise PathContainmentError(f"{candidate} escapes root {root_real}") from exc
    if common != str(root_real):
        raise PathContainmentError(f"{candidate} escapes root {root_real}")
    return candidate_real


def case_dir(documents_root: str | os.PathLike[str], client: str, case_number: str) -> Path:
    """The directory for one client/case, guaranteed under ``documents_root``."""
    return safe_join(documents_root, client, case_number)
