from __future__ import annotations

from pathlib import Path

import bump_version
import pytest


def _write_package(root: Path, name: str, version: str) -> Path:
    pkg_dir = root / name
    pkg_dir.mkdir(parents=True)
    pyproject = pkg_dir / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\ndependencies = []\n\n'
        f'[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    return pyproject


@pytest.fixture(autouse=True)
def _fake_packages_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    monkeypatch.setattr(bump_version, "PACKAGES_DIR", packages_dir)
    monkeypatch.setattr(bump_version, "REPO_ROOT", tmp_path)
    return packages_dir


def test_parse_semver_valid() -> None:
    assert bump_version.parse_semver("1.2.3") == (1, 2, 3)


def test_parse_semver_rejects_garbage() -> None:
    with pytest.raises(bump_version.VersionError):
        bump_version.parse_semver("not-a-version")


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ("1.2.3", "major", "2.0.0"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "9.9.9", "9.9.9"),
    ],
)
def test_bump(current: str, target: str, expected: str) -> None:
    assert bump_version.bump(current, target) == expected


def test_bump_rejects_malformed_explicit_version() -> None:
    with pytest.raises(bump_version.VersionError):
        bump_version.bump("1.2.3", "not-a-version")


def test_current_version_reads_lockstep_version(_fake_packages_dir: Path) -> None:
    _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    _write_package(_fake_packages_dir, "pkg-b", "0.1.0")
    assert bump_version.current_version() == "0.1.0"


def test_current_version_raises_when_out_of_lockstep(_fake_packages_dir: Path) -> None:
    _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    _write_package(_fake_packages_dir, "pkg-b", "0.2.0")
    with pytest.raises(bump_version.VersionError, match="out of lockstep"):
        bump_version.current_version()


def test_rewrite_version_updates_only_the_version_line(_fake_packages_dir: Path) -> None:
    pyproject = _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    before = pyproject.read_text(encoding="utf-8")

    bump_version._rewrite_version(pyproject, "0.2.0")

    after = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in after
    assert 'version = "0.1.0"' not in after
    # everything else on the file is untouched
    assert before.replace('version = "0.1.0"', 'version = "0.2.0"') == after


def test_main_dry_run_writes_nothing(_fake_packages_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    before = pyproject.read_text(encoding="utf-8")

    exit_code = bump_version.main(["minor", "--dry-run"])

    assert exit_code == 0
    assert pyproject.read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert "0.1.0 -> 0.2.0" in out
    assert "dry run" in out


def test_main_writes_every_package_and_runs_uv_lock(
    _fake_packages_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject_a = _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    pyproject_b = _write_package(_fake_packages_dir, "pkg-b", "0.1.0")

    lock_calls: list[bool] = []
    monkeypatch.setattr(bump_version, "_run_uv_lock", lambda: lock_calls.append(True))

    exit_code = bump_version.main(["patch"])

    assert exit_code == 0
    assert 'version = "0.1.1"' in pyproject_a.read_text(encoding="utf-8")
    assert 'version = "0.1.1"' in pyproject_b.read_text(encoding="utf-8")
    assert lock_calls == [True]
    out = capsys.readouterr().out
    assert "0.1.1" in out
    assert "git tag v0.1.1" in out


def test_main_reports_out_of_lockstep_error_on_stderr(
    _fake_packages_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_package(_fake_packages_dir, "pkg-a", "0.1.0")
    _write_package(_fake_packages_dir, "pkg-b", "0.2.0")

    exit_code = bump_version.main(["patch"])

    assert exit_code == 1
    assert "out of lockstep" in capsys.readouterr().err
