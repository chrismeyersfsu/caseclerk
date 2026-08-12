"""GitHub Releases update check and the detached self-update spawn.

Network access is isolated behind an injectable ``httpx.Client`` and the
`uv tool install` spawn behind an injectable ``spawn`` callable, so
tests exercise the real logic without touching the network or actually
launching `uv`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import total_ordering
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

import httpx

from caseclerk_core import db

logger = logging.getLogger(__name__)

REPO = "chrismeyersfsu/caseclerk"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DISTRIBUTION_NAME = "caseclerk-cli"
REQUEST_TIMEOUT_SECONDS = 5.0

_META_LAST_CHECK = "updates.last_check_at"
_META_AVAILABLE_VERSION = "updates.available_version"

_SEMVER_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z.-]+))?$")


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    def __lt__(self, other: SemVer) -> bool:
        self_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if self_core != other_core:
            return self_core < other_core
        if self.prerelease == other.prerelease:
            return False
        if self.prerelease is None:
            return False  # a normal release outranks any prerelease of the same core version
        if other.prerelease is None:
            return True
        return self.prerelease < other.prerelease


def parse_semver(text: str) -> SemVer:
    match = _SEMVER_RE.match(text.strip())
    if not match:
        raise ValueError(f"not a semver string: {text!r}")
    return SemVer(
        major=int(match["major"]),
        minor=int(match["minor"]),
        patch=int(match["patch"]),
        prerelease=match["pre"],
    )


def is_newer(candidate: str, current: str) -> bool:
    return parse_semver(candidate) > parse_semver(current)


def current_version(distribution: str = DISTRIBUTION_NAME) -> str:
    try:
        return pkg_version(distribution)
    except PackageNotFoundError:
        return "0.0.0"


def fetch_latest_release_tag(client: httpx.Client | None = None) -> str | None:
    """The latest release tag (e.g. 'v1.2.0'), or None on any network/API/rate-limit problem."""
    owns_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        response = http.get(RELEASES_LATEST_URL, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        payload = response.json()
        tag = payload.get("tag_name") if isinstance(payload, dict) else None
        return str(tag) if tag else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("update check failed, treating as no update available: %s", exc)
        return None
    finally:
        if owns_client:
            http.close()


def check_for_update(
    conn: sqlite3.Connection,
    *,
    check_interval_hours: int = 24,
    current: str | None = None,
    client: httpx.Client | None = None,
    now: Callable[[], datetime] | None = None,
) -> str | None:
    """Newer version tag if one is available, re-querying GitHub at most once per interval."""
    now_fn = now or (lambda: datetime.now(UTC))
    at = now_fn()
    current_version_str = current or current_version()

    last_check_raw = db.get_meta(conn, _META_LAST_CHECK)
    if last_check_raw is not None:
        last_check = datetime.fromisoformat(last_check_raw)
        if at - last_check < timedelta(hours=check_interval_hours):
            cached = db.get_meta(conn, _META_AVAILABLE_VERSION)
            return cached or None

    tag = fetch_latest_release_tag(client=client)
    db.set_meta(conn, _META_LAST_CHECK, at.isoformat())

    if tag is None:
        return None
    try:
        newer = is_newer(tag, current_version_str)
    except ValueError:
        logger.warning("latest release tag is not valid semver, ignoring: %s", tag)
        return None

    db.set_meta(conn, _META_AVAILABLE_VERSION, tag if newer else "")
    return tag if newer else None


# Return type is deliberately `object`, not Popen[bytes]: callers only care that the
# update was launched, which keeps test doubles free to return anything (or None).
SpawnFn = Callable[[list[str]], object]


def _default_spawn(args: list[str]) -> object:
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def apply_update(version_tag: str, *, spawn: SpawnFn = _default_spawn) -> object:
    """Spawn a detached `uv tool install` for version_tag; takes effect on the host's next restart."""
    source = f"git+https://github.com/{REPO}@{version_tag}"
    args = ["uv", "tool", "install", "--force", "--from", source, DISTRIBUTION_NAME]
    logger.info("spawning self-update: %s", " ".join(args))
    return spawn(args)
