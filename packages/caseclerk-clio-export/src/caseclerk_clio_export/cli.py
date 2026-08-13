"""Command-line interface for clio-export."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from . import api, export, oauth
from .config import DEFAULT_CALLBACK_PORT, REGION_BASE_URLS, Credentials, credentials_path

# Refresh ahead of expiry so a pull never starts with a nearly-dead token.
REFRESH_MARGIN_SECONDS = 86400.0


def _cmd_auth(args: argparse.Namespace) -> int:
    client_id = args.client_id or input("Clio app Client ID (App Key): ").strip()
    client_secret = args.client_secret or getpass.getpass("Clio app Client Secret (App Secret): ").strip()
    creds = Credentials(client_id=client_id, client_secret=client_secret, region=args.region)
    redirect_uri = f"http://127.0.0.1:{args.port}/callback"
    state = secrets.token_urlsafe(16)
    url = oauth.authorize_url(creds.base_url, client_id, redirect_uri, state)
    server = oauth.open_callback_server(args.port)
    print(f"Your Clio application's Redirect URI must include:\n  {redirect_uri}\n")
    if args.no_browser:
        print(f"Open this URL in a browser logged into Clio:\n  {url}\n", flush=True)
    else:
        print("Opening your browser; log into Clio and click Allow.")
        print(f"If nothing opens, use this URL:\n  {url}\n", flush=True)
        webbrowser.open(url)
    print("Waiting for authorization (5 minute timeout)...", flush=True)
    code = oauth.wait_for_code(server, state)
    creds.token = oauth.exchange_code(creds.base_url, client_id, client_secret, code, redirect_uri)
    path = creds.save()
    print(f"Authorized. Tokens saved to {path}")
    print("You can now run `clio-export pull` (interactively or from cron).")
    return 0


def _load_client() -> api.ClioClient:
    creds = Credentials.load()

    def refresh() -> str:
        creds.token = oauth.refresh_access_token(
            creds.base_url, creds.client_id, creds.client_secret, creds.token
        )
        creds.save()
        access_token: str = creds.token["access_token"]
        return access_token

    expires_at = float(creds.token.get("expires_at") or 0)
    if not creds.token.get("access_token") or time.time() > expires_at - REFRESH_MARGIN_SECONDS:
        refresh()
    return api.ClioClient(creds.base_url, creds.token["access_token"], refresh=refresh)


def _cmd_pull(args: argparse.Namespace) -> int:
    client = _load_client()
    out_dir = Path(args.out)
    if args.snapshot:
        out_dir = out_dir / datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    resources = [args.only] if args.only else list(export.RESOURCES)
    results = {}
    for name in resources:
        print(f"Pulling {name}...", flush=True)
        results[name] = export.pull_records(client, name, updated_since=args.updated_since)
        print(f"  {len(results[name])} records")
    manifest = export.write_exports(out_dir, results, write_csv=not args.skip_csv)
    summary = ", ".join(f"{k}: {v}" for k, v in manifest["counts"].items())
    print(f"Wrote {out_dir}/ ({summary})")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    path = credentials_path()
    if not path.exists():
        print(f"Not authorized: no credentials at {path}. Run `clio-export auth`.")
        return 1
    creds = Credentials.load()
    print(f"Credentials file: {path}")
    print(f"Region: {creds.region} ({creds.base_url})")
    if not creds.token.get("access_token"):
        print("No token stored; run `clio-export auth`.")
        return 1
    expires_at = creds.token.get("expires_at")
    if expires_at:
        remaining_days = (float(expires_at) - time.time()) / 86400
        when = datetime.fromtimestamp(float(expires_at), tz=UTC)
        if remaining_days <= 0:
            print("Access token: expired (will auto-refresh on next pull)")
        else:
            print(f"Access token: expires {when.isoformat(timespec='seconds')} ({remaining_days:.1f} days)")
    has_refresh = "yes" if creds.token.get("refresh_token") else "no"
    print(f"Refresh token stored: {has_refresh}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clio-export",
        description="Export contacts and matters from Clio Manage.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="One-time interactive OAuth authorization against Clio")
    p_auth.add_argument("--client-id", help="Clio developer app key")
    p_auth.add_argument("--client-secret", help="Clio developer app secret")
    p_auth.add_argument(
        "--region",
        choices=sorted(REGION_BASE_URLS),
        default="us",
        help="Clio region (default: us)",
    )
    p_auth.add_argument(
        "--port",
        type=int,
        default=DEFAULT_CALLBACK_PORT,
        help=f"Localhost callback port (default: {DEFAULT_CALLBACK_PORT})",
    )
    p_auth.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL instead of opening a browser",
    )
    p_auth.set_defaults(func=_cmd_auth)

    p_pull = sub.add_parser("pull", help="Pull contacts and matters into JSON/CSV files")
    p_pull.add_argument("--out", default="data", help="Output directory (default: ./data)")
    p_pull.add_argument(
        "--only",
        choices=sorted(export.RESOURCES),
        help="Pull a single resource instead of all",
    )
    p_pull.add_argument("--updated-since", help="Only records updated after this ISO-8601 timestamp")
    p_pull.add_argument(
        "--snapshot",
        action="store_true",
        help="Write into a timestamped subdirectory instead of overwriting",
    )
    p_pull.add_argument("--skip-csv", action="store_true", help="Write JSON only, no CSV")
    p_pull.set_defaults(func=_cmd_pull)

    p_status = sub.add_parser("status", help="Show authorization status")
    p_status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except (FileNotFoundError, TimeoutError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
