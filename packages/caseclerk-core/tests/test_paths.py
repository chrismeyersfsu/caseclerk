from pathlib import Path

import pytest

from caseclerk_core.paths import PathContainmentError, case_dir, safe_join


def test_safe_join_within_root(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    (root / "Alvarez, Maria" / "2026-0142").mkdir(parents=True)
    result = safe_join(root, "Alvarez, Maria", "2026-0142")
    assert result == (root / "Alvarez, Maria" / "2026-0142").resolve()


def test_safe_join_rejects_dotdot_traversal(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    with pytest.raises(PathContainmentError):
        safe_join(root, "..", "outside")


def test_safe_join_rejects_absolute_segment(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    root.mkdir()
    with pytest.raises(PathContainmentError):
        safe_join(root, "/etc/passwd")


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "confidential.txt").write_text("nope")
    (root / "escape").symlink_to(secret, target_is_directory=True)

    with pytest.raises(PathContainmentError):
        safe_join(root, "escape", "confidential.txt")


def test_safe_join_allows_a_symlink_that_stays_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    real_dir = root / "Alvarez, Maria" / "2026-0142"
    real_dir.mkdir(parents=True)
    (real_dir / "notes.txt").write_text("hi")
    link = root / "Alvarez, Maria" / "alias"
    link.symlink_to(real_dir, target_is_directory=True)

    result = safe_join(root, "Alvarez, Maria", "alias", "notes.txt")
    assert result == (real_dir / "notes.txt").resolve()


def test_case_dir_builds_under_root(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    (root / "Barrett Holdings LLC" / "2026-0201").mkdir(parents=True)
    result = case_dir(root, "Barrett Holdings LLC", "2026-0201")
    assert result == (root / "Barrett Holdings LLC" / "2026-0201").resolve()


def test_case_dir_rejects_traversal_in_case_number(tmp_path: Path) -> None:
    root = tmp_path / "clio"
    root.mkdir()
    with pytest.raises(PathContainmentError):
        case_dir(root, "Alvarez, Maria", "../../etc")
