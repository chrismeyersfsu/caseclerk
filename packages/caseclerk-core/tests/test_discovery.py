import os
import sys
from pathlib import Path

import pytest

from caseclerk_core.discovery import discover, looks_like_case_number, score_candidate


def test_looks_like_case_number() -> None:
    assert looks_like_case_number("2026-0142")
    assert looks_like_case_number("A123")
    assert not looks_like_case_number("Miscellaneous")
    assert not looks_like_case_number(".hidden123")
    assert not looks_like_case_number("no digits here")


def _build_clio_tree(root: Path) -> None:
    (root / "Alvarez, Maria" / "2026-0142").mkdir(parents=True)
    (root / "Alvarez, Maria" / "2026-0201").mkdir(parents=True)
    (root / "Barrett Holdings LLC" / "2026-0310").mkdir(parents=True)
    (root / "Notes" / "misc").mkdir(parents=True)  # no case-number-shaped subdir


def test_score_candidate_counts_client_dirs_with_case_subdirs(tmp_path: Path) -> None:
    _build_clio_tree(tmp_path)
    assert score_candidate(tmp_path) == 2


def test_score_candidate_zero_for_non_directory(tmp_path: Path) -> None:
    assert score_candidate(tmp_path / "does-not-exist") == 0


@pytest.mark.skipif(sys.platform == "win32", reason="chmod-based permission denial isn't portable to Windows")
def test_score_candidate_skips_unreadable_entries_instead_of_crashing(tmp_path: Path) -> None:
    """Regression test: real mounts (/mnt, /media, /Volumes) routinely contain entries the
    current user can't stat; one bad entry must not abort scoring every other candidate."""
    _build_clio_tree(tmp_path)
    locked = tmp_path / "Locked Client"
    locked.mkdir()
    (locked / "2026-9999").mkdir()
    os.chmod(locked, 0o000)
    try:
        # must not raise, and the two real client dirs are still counted
        assert score_candidate(tmp_path) == 2
    finally:
        os.chmod(locked, 0o755)  # restore so tmp_path cleanup can remove it


def test_discover_ranks_injected_roots_by_score(tmp_path: Path) -> None:
    good = tmp_path / "good"
    _build_clio_tree(good)
    better = tmp_path / "better"
    _build_clio_tree(better)
    (better / "Extra Client" / "2026-9999").mkdir(parents=True)
    empty = tmp_path / "empty"
    empty.mkdir()

    ranked = discover(roots=[empty, good, better])
    assert [c.path for c in ranked] == [better, good]
    assert ranked[0].score == 3
    assert ranked[1].score == 2


def test_discover_returns_empty_for_no_matches(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert discover(roots=[empty]) == []
