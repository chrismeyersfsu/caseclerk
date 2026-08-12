from __future__ import annotations

import hashlib
import io
import json
import platform as platform_module
import stat
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from caseclerk_cli import cloudflared


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloudflared, "data_dir", lambda: tmp_path / "data")


def _make_tgz(binary_contents: bytes, *, member_name: str = "cloudflared") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(binary_contents)
        tar.addfile(info, io.BytesIO(binary_contents))
    return buf.getvalue()


def test_binary_name_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert cloudflared._binary_name() == "cloudflared.exe"
    monkeypatch.setattr(sys, "platform", "linux")
    assert cloudflared._binary_name() == "cloudflared"


def test_asset_name_windows_ignores_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert cloudflared._asset_name() == "cloudflared-windows-amd64.exe"


def test_asset_name_linux_by_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    assert cloudflared._asset_name() == "cloudflared-linux-amd64"
    monkeypatch.setattr(platform_module, "machine", lambda: "aarch64")
    assert cloudflared._asset_name() == "cloudflared-linux-arm64"


def test_asset_name_darwin_by_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform_module, "machine", lambda: "x86_64")
    assert cloudflared._asset_name() == "cloudflared-darwin-amd64.tgz"
    monkeypatch.setattr(platform_module, "machine", lambda: "arm64")
    assert cloudflared._asset_name() == "cloudflared-darwin-arm64.tgz"


def test_find_bundled_none_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloudflared, "is_frozen", lambda: False)
    assert cloudflared.find_bundled() is None


def test_find_bundled_present_when_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cloudflared, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(frozen_dir / "caseclerk"))

    assert cloudflared.find_bundled() is None  # not there yet

    exe = frozen_dir / "cloudflared"
    exe.write_text("bundled binary")
    assert cloudflared.find_bundled() == exe


def test_find_cached(tmp_path: Path) -> None:
    assert cloudflared.find_cached() is None
    cached = cloudflared._cached_path()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("cached binary")
    assert cloudflared.find_cached() == cached


def test_resolve_prefers_bundled_over_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(cloudflared, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(frozen_dir / "caseclerk"))
    bundled = frozen_dir / "cloudflared"
    bundled.write_text("bundled binary")

    cached = cloudflared._cached_path()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("cached binary")

    assert cloudflared.resolve() == bundled


def test_resolve_prefers_cached_over_download(tmp_path: Path) -> None:
    cached = cloudflared._cached_path()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("cached binary")

    assert cloudflared.resolve() == cached


def test_resolve_raises_when_download_disabled_and_nothing_available() -> None:
    with pytest.raises(cloudflared.CloudflaredError, match="downloads are disabled"):
        cloudflared.resolve(allow_download=False)


def test_download_verifies_checksum_and_caches_plain_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    contents = b"fake cloudflared.exe bytes"
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setitem(cloudflared._ASSET_SHA256, "cloudflared-windows-amd64.exe", digest)

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=contents)

    with _client(handler) as http:
        result = cloudflared.download(client=http)

    assert requested_urls == [
        f"https://github.com/cloudflare/cloudflared/releases/download/"
        f"{cloudflared.CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe"
    ]
    assert result == cloudflared._cached_path()
    assert result.read_bytes() == contents


def test_download_extracts_tgz_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real OS running this test, captured *before* sys.platform gets
    # monkeypatched below to force cloudflared's darwin/.tgz code path --
    # os.chmod's actual effect on a file is governed by the real OS, not by
    # what the code under test believes sys.platform is, so the exec-bit
    # assertion further down must gate on this, not the monkeypatched value.
    real_platform = sys.platform
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform_module, "machine", lambda: "arm64")
    binary_contents = b"fake darwin cloudflared binary"
    tgz_bytes = _make_tgz(binary_contents)
    digest = hashlib.sha256(tgz_bytes).hexdigest()
    monkeypatch.setitem(cloudflared._ASSET_SHA256, "cloudflared-darwin-arm64.tgz", digest)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=tgz_bytes)

    with _client(handler) as http:
        result = cloudflared.download(client=http)

    assert result == cloudflared._cached_path()
    assert result.read_bytes() == binary_contents
    if real_platform != "win32":
        assert result.stat().st_mode & stat.S_IEXEC


def test_download_rejects_checksum_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(cloudflared._ASSET_SHA256, "cloudflared-windows-amd64.exe", "0" * 64)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not what we expected")

    with _client(handler) as http, pytest.raises(cloudflared.CloudflaredError, match="checksum mismatch"):
        cloudflared.download(client=http)
    assert not cloudflared._cached_path().exists()


def test_download_tolerates_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with _client(handler) as http, pytest.raises(cloudflared.CloudflaredError, match="failed to download"):
        cloudflared.download(client=http)


def test_download_reports_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    contents = b"x" * 1000
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setitem(cloudflared._ASSET_SHA256, "cloudflared-windows-amd64.exe", digest)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=contents, headers={"content-length": str(len(contents))})

    messages: list[str] = []
    with _client(handler) as http:
        cloudflared.download(progress=messages.append, client=http)

    assert any("Downloading cloudflared" in m for m in messages)
    assert any("ready at" in m for m in messages)


def test_resolve_downloads_when_nothing_cached_or_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    contents = b"fresh cloudflared.exe bytes"
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setitem(cloudflared._ASSET_SHA256, "cloudflared-windows-amd64.exe", digest)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=contents)

    with _client(handler) as http:
        result = cloudflared.resolve(client=http)

    assert result == cloudflared._cached_path()
    assert result.read_bytes() == contents


def test_installed_version_runs_the_binary() -> None:
    # sys.executable is a real, directly-executable binary on every platform,
    # unlike a hand-written POSIX shebang script (Windows' CreateProcess can't
    # launch those directly -- "not a valid Win32 application"). This proves
    # installed_version() runs the given binary and captures its output,
    # regardless of what a real cloudflared binary's own output looks like.
    output = cloudflared.installed_version(Path(sys.executable))
    assert output is not None
    assert "Python" in output or "python" in output.lower()


def test_installed_version_none_on_missing_binary(tmp_path: Path) -> None:
    assert cloudflared.installed_version(tmp_path / "does-not-exist") is None


def test_source_label_downloaded(tmp_path: Path) -> None:
    cached = cloudflared._cached_path()
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("x")
    assert cloudflared.source_label(cached) == "downloaded"


def test_source_label_bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    monkeypatch.setattr(cloudflared, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(frozen_dir / "caseclerk"))
    bundled = frozen_dir / "cloudflared"
    bundled.write_text("x")
    assert cloudflared.source_label(bundled) == "bundled"


def test_source_label_unknown_for_arbitrary_path(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere" / "cloudflared"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    assert cloudflared.source_label(other) == "unknown"


# --- Non-interactive tunnel setup (install_credentials) ---


def _write_fake_credentials(
    tmp_path: Path, *, tunnel_id: str = "11111111-2222-3333-4444-555555555555"
) -> Path:
    path = tmp_path / "source-credentials.json"
    path.write_text(
        json.dumps({"AccountTag": "abc123", "TunnelSecret": "supersecret", "TunnelID": tunnel_id}),
        encoding="utf-8",
    )
    return path


def test_yaml_single_quoted_escapes_embedded_quotes() -> None:
    assert cloudflared._yaml_single_quoted("plain") == "'plain'"
    assert cloudflared._yaml_single_quoted("O'Brien") == "'O''Brien'"


def test_yaml_single_quoted_round_trips_windows_paths() -> None:
    # the whole point of single- (not double-) quoting: no backslash escaping
    windows_path = r"C:\Users\attorney\AppData\Local\caseclerk\cloudflared\x.json"
    assert cloudflared._yaml_single_quoted(windows_path) == f"'{windows_path}'"


def test_parse_tunnel_id_reads_tunnel_id_field(tmp_path: Path) -> None:
    creds = _write_fake_credentials(tmp_path, tunnel_id="my-tunnel-id")
    assert cloudflared._parse_tunnel_id(creds) == "my-tunnel-id"


def test_parse_tunnel_id_rejects_missing_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "a tunnel credentials file"}), encoding="utf-8")
    with pytest.raises(cloudflared.CloudflaredError, match="TunnelID"):
        cloudflared._parse_tunnel_id(bad)


def test_parse_tunnel_id_rejects_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    with pytest.raises(cloudflared.CloudflaredError, match="could not read"):
        cloudflared._parse_tunnel_id(bad)


def test_config_path_under_data_dir(tmp_path: Path) -> None:
    assert cloudflared.config_path() == tmp_path / "data" / "cloudflared" / "config.yml"


def test_install_credentials_writes_config_and_copies_credentials(tmp_path: Path) -> None:
    source = _write_fake_credentials(tmp_path, tunnel_id="tunnel-abc")

    result = cloudflared.install_credentials(source, hostname="files.example.com", port=8787)

    assert result.tunnel_id == "tunnel-abc"
    assert result.credentials_path == tmp_path / "data" / "cloudflared" / "tunnel-abc.json"
    assert result.config_path == cloudflared.config_path()
    assert result.credentials_path.read_bytes() == source.read_bytes()

    config_text = result.config_path.read_text(encoding="utf-8")
    assert "tunnel: 'tunnel-abc'" in config_text
    assert f"credentials-file: '{result.credentials_path}'" in config_text
    assert "hostname: 'files.example.com'" in config_text
    assert "service: 'http://127.0.0.1:8787'" in config_text
    assert "http_status:404" in config_text


def test_install_credentials_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(cloudflared.CloudflaredError, match="not found"):
        cloudflared.install_credentials(tmp_path / "nope.json", hostname="x.example.com", port=8787)


def test_install_credentials_is_idempotent(tmp_path: Path) -> None:
    source = _write_fake_credentials(tmp_path, tunnel_id="tunnel-abc")

    first = cloudflared.install_credentials(source, hostname="one.example.com", port=8787)
    second = cloudflared.install_credentials(source, hostname="two.example.com", port=9000)

    assert first.config_path == second.config_path
    config_text = second.config_path.read_text(encoding="utf-8")
    assert "two.example.com" in config_text
    assert "one.example.com" not in config_text
