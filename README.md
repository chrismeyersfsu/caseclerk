# CaseClerk

[![checks-core](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-core.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-core.yml)
[![checks-pipeline](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-pipeline.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-pipeline.yml)
[![checks-artifacts](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-artifacts.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-artifacts.yml)
[![checks-mcp](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-mcp.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-mcp.yml)
[![checks-cli](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-cli.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-cli.yml)
[![checks-fixtures](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-fixtures.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/checks-fixtures.yml)
[![e2e](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/e2e.yml/badge.svg?branch=main)](https://github.com/chrismeyersfsu/caseclerk/actions/workflows/e2e.yml)

CaseClerk is an [MCP](https://modelcontextprotocol.io) server for a small law firm's case files. It lets an MCP host (Claude Desktop, Claude Code, or any other MCP client) read and search the documents for exactly one client and case at a time, and draft an Outlook-ready email into that case's folder — never mixing clients, never touching anything outside the case it was asked about. Everything else — chat, dictation, model choice, session history — is left to the host; CaseClerk only builds what is specific to running a law practice's document folder.

## Architecture

```
MCP host (Claude Desktop / Claude Code / any MCP client)
        │  stdio (JSON-RPC)
        ▼
   caseclerk serve  ──────────────►  SQLite index (clients, cases, documents,
   (tools + prompt)                   chunks + FTS5, jobs, meta)
        │
        ▼
   Case documents folder on disk
   <documentsRoot>/<Client>/<CaseNumber>/**
```

Documents are never sent to the model whole. A background scan walks the documents folder, converts each document to markdown (`.docx` via `mammoth`, `.pdf` via `pdfminer.six`, `.txt`/`.md` as-is), chunks it, and indexes it into SQLite with full-text search — that index, not the filesystem, is what the MCP tools query. The only write path is `save_email_draft`, which writes a matched `.eml`/`.txt` pair into `<case>/emails-generated/` and nothing else.

## Install

### Claude Desktop

```sh
uv tool install --from git+https://github.com/chrismeyersfsu/caseclerk caseclerk-cli
caseclerk init --write-claude-config
```

`init` discovers your case documents folder, writes `config.json`, and (with `--write-claude-config`, or when you confirm interactively) merges a `caseclerk` entry into `claude_desktop_config.json`. Restart Claude Desktop afterward.

### Claude Code

```sh
uv tool install --from git+https://github.com/chrismeyersfsu/caseclerk caseclerk-cli
caseclerk init
claude mcp add caseclerk -- caseclerk serve
```

### Any other MCP client

CaseClerk speaks plain MCP over stdio — the command any host needs is `caseclerk serve`. No client-specific code exists or is planned; if your host can launch a local stdio MCP server, it can use CaseClerk.

## CLI reference

| Command | Description |
|---|---|
| `caseclerk serve` | Run the MCP server over stdio (what an MCP host launches) |
| `caseclerk init [--yes] [--write-claude-config]` | Discover the case documents folder, write `config.json`, print/write MCP client setup |
| `caseclerk process [--concurrency N]` | Scan `documentsRoot` for new/changed documents and drain the processing queue once |
| `caseclerk status` | Show queue/indexed/failed counts and any cached update-available version |
| `caseclerk failures` | List every document currently in the failed state |
| `caseclerk retry <document_id>` / `caseclerk retry --all-failed` | Requeue a document, or every failed document |
| `caseclerk config path` / `get <key>` / `set <key> <value>` | Read or update `config.json` by dotted camelCase key (e.g. `processing.concurrency`) |
| `caseclerk update` | Check GitHub Releases and apply an update on the spot if one is available |
| `caseclerk doctor` | Verify SQLite has FTS5, `uv` is on `PATH` (or that this is a packaged binary, which needs no `uv`), config is valid, `documentsRoot` exists, the db is writable, and (if `share` is configured) cloudflared's source + version |
| `caseclerk serve --transport http [--port N]` | Run the MCP server over streamable HTTP instead of stdio, bound to `127.0.0.1` only (no `--host` flag exists) |
| `caseclerk share setup [--credentials <path> --hostname <host> [--tunnel-name <name>]]` | Resolve or download a managed cloudflared binary (no install/PATH/admin needed) without starting anything; with `--credentials`, also installs a tunnel non-interactively (no `cloudflared tunnel login` on this machine) |
| `caseclerk share start` / `stop` / `status` | Start or stop the HTTP transport + a cloudflared tunnel as detached processes (for ChatGPT); show whether it's running, the public URL, and recent audit entries |
| `caseclerk share shortcuts` | Create Desktop shortcuts that toggle sharing on/off with a double-click (Windows only) |
| `caseclerk audit [--limit N]` | Show the most recent HTTP-transport tool calls (stdio never writes these) |

During development, run any of these as `uv run caseclerk <command>` from the repo root instead of installing.

## Configuration

Config lives at `platformdirs.user_config_dir("caseclerk")/config.json`. Precedence is `CASECLERK_*` environment variables > the file > built-in defaults.

```json
{
  "documentsRoot": "A:\\",
  "emailsFolderName": "emails-generated",
  "emailFileNameTemplate": "{yyyy}-{mm}-{dd}-{slug}",
  "processing": { "concurrency": 2, "watch": true, "ignore": ["emails-generated/**"] },
  "updates": { "auto": true, "checkIntervalHours": 24 },
  "summarization": { "enabled": false, "provider": "anthropic", "baseUrl": "", "apiKeyEnv": "", "model": "" },
  "share": { "hostname": null, "port": 8787, "tunnelName": "caseclerk" },
  "promptsDir": null
}
```

| Env var | Overrides |
|---|---|
| `CASECLERK_DOCUMENTS_ROOT` | `documentsRoot` |
| `CASECLERK_EMAILS_FOLDER_NAME` | `emailsFolderName` |
| `CASECLERK_EMAIL_FILE_NAME_TEMPLATE` | `emailFileNameTemplate` |
| `CASECLERK_PROCESSING_CONCURRENCY` | `processing.concurrency` |
| `CASECLERK_PROCESSING_WATCH` | `processing.watch` |
| `CASECLERK_PROCESSING_IGNORE` | `processing.ignore` (comma-separated) |
| `CASECLERK_UPDATES_AUTO` | `updates.auto` |
| `CASECLERK_UPDATES_CHECK_INTERVAL_HOURS` | `updates.checkIntervalHours` |
| `CASECLERK_SUMMARIZATION_ENABLED` | `summarization.enabled` |
| `CASECLERK_SUMMARIZATION_PROVIDER` | `summarization.provider` |
| `CASECLERK_SUMMARIZATION_BASE_URL` | `summarization.baseUrl` |
| `CASECLERK_SUMMARIZATION_API_KEY_ENV` | `summarization.apiKeyEnv` (a var **name**, never a secret value) |
| `CASECLERK_SUMMARIZATION_MODEL` | `summarization.model` |
| `CASECLERK_SHARE_HOSTNAME` | `share.hostname` |
| `CASECLERK_SHARE_PORT` | `share.port` |
| `CASECLERK_SHARE_TUNNEL_NAME` | `share.tunnelName` |
| `CASECLERK_PROMPTS_DIR` | `promptsDir` |
| `CASECLERK_CONFIG_DIR`, `CASECLERK_DATA_DIR` | relocate the config/data directories themselves (how the test suite stays off your real machine) |

The `emails-generated` folder is always excluded from scanning and indexing, regardless of `processing.ignore`.

## Development

```sh
export PATH="$HOME/.local/bin:$PATH"   # if uv isn't already on PATH
uv python pin 3.12
uv sync --all-packages

uv run ruff check . && uv run ruff format --check .
uv run mypy packages
uv run pytest                          # unit suite only (fast); e2e is excluded by default

# build a synthetic documents drive to poke at locally
uv run python -m caseclerk_fixtures /tmp/documents-fixture
CASECLERK_DOCUMENTS_ROOT=/tmp/documents-fixture uv run caseclerk process
CASECLERK_DOCUMENTS_ROOT=/tmp/documents-fixture uv run caseclerk status

# full end-to-end run: spawns the real `caseclerk serve` over stdio against a
# fresh fixture drive and writes a human-readable HTML report
uv run pytest -m e2e tests/e2e
open test-artifacts/e2e/index.html     # or your platform's equivalent
```

Each package under `packages/` is an independent workspace member with its own `pyproject.toml`, `src/`, and `tests/`; `tests/e2e/` holds the cross-package end-to-end test. CI (`.github/workflows/`) mirrors this: a reusable `_checks.yml` runs ruff/mypy/pytest for one package on a three-OS matrix, and a thin per-package workflow calls it, path-filtered so a change only re-runs the packages it could affect; `release.yml` runs the same checks plus the e2e suite for every package before publishing a tagged release.

Every package's version stays in lockstep. To cut a release: `uv run scripts/bump_version.py <major|minor|patch|X.Y.Z>` (add `--dry-run` to preview) rewrites every `packages/*/pyproject.toml` and re-locks, then commit and tag as it tells you to.

## Releases & updates

Tagged pushes (`vX.Y.Z`) run the full check + e2e suite on all three OSes and, once green, publish a [GitHub Release](https://github.com/chrismeyersfsu/caseclerk/releases) with every package's wheel and sdist attached, plus the e2e HTML report as a zip. Install (or reinstall) from the latest release with:

```sh
uv tool install --force "caseclerk-cli @ git+https://github.com/chrismeyersfsu/caseclerk@vX.Y.Z#subdirectory=packages/caseclerk-cli"
```

Once installed, `caseclerk serve` and `caseclerk status` check GitHub Releases for a newer version at most once every `updates.checkIntervalHours` (24h by default) and cache the result; `caseclerk status`/`doctor` and the `processing_status`/`get_settings` MCP tools surface it. Run `caseclerk update` any time to check on demand and apply immediately — it re-runs the same `uv tool install` command against the newer tag; the new version takes effect the next time your MCP host restarts the server. `caseclerk --version` prints what's currently installed.

## Remote access (ChatGPT)

Claude Desktop and Claude Code run CaseClerk locally over stdio — nothing is ever exposed to the network. ChatGPT's custom connectors, by contrast, connect from OpenAI's cloud, so they need an internet-reachable HTTPS URL. CaseClerk supports this with a streamable-HTTP transport, protected by an embedded OAuth 2.1 authorization server, exposed only through an outbound-only [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) you bring up deliberately.

CaseClerk manages its own cloudflared binary — there's nothing to separately install. `caseclerk share setup` resolves one (a copy bundled with a packaged build, a previously downloaded copy, or a pinned, checksum-verified download from cloudflare/cloudflared's GitHub releases, in that order) into CaseClerk's own data directory; it never touches `PATH` and never needs admin.

There are two ways to point that binary at a tunnel, depending on whose machine you're setting up:

### Option A: interactive setup, on a machine you're happy to log in on

1. **Fetch cloudflared and log in to your Cloudflare account:**
   ```sh
   caseclerk share setup   # downloads/resolves cloudflared, prints its path
   ```
   Use the printed path to run the one-time Cloudflare login (opens a browser):
   ```sh
   <path from share setup> tunnel login
   ```
2. **Create a named tunnel** and note the hostname you want to use (a subdomain of a domain already in your Cloudflare account):
   ```sh
   <path from share setup> tunnel create caseclerk
   <path from share setup> tunnel route dns caseclerk caseclerk.yourdomain.com
   ```
   That's it for cloudflared configuration — CaseClerk runs the tunnel with `cloudflared tunnel run --url http://127.0.0.1:<port> <tunnelName>`, so no `config.yml`/ingress file is needed.
3. **Point CaseClerk at it:**
   ```sh
   caseclerk config set share.hostname caseclerk.yourdomain.com
   caseclerk doctor    # confirms cloudflared is ready now that share is configured
   ```

### Option B: non-interactive setup, for a machine that should never log in (e.g. the attorney's)

`cloudflared tunnel login` and `tunnel create` need to talk to the Cloudflare API with your account credentials — appropriate on your own machine, not something to run on someone else's. Do those two steps once on **your** machine (Option A, steps 1–2, up through `tunnel create`), which leaves behind a tunnel *credentials* JSON file (printed by `tunnel create`, normally under `~/.cloudflared/<tunnel-id>.json`). Copy just that JSON file to the target machine, then there:

```sh
caseclerk share setup --credentials <path-to-tunnel-id.json> --hostname caseclerk.yourdomain.com
```

This resolves/downloads cloudflared, installs the credentials into CaseClerk's data directory, writes cloudflared's `config.yml` (tunnel id, credentials path, and an ingress rule routing the hostname to `http://127.0.0.1:<share.port>`), and sets `share.hostname`/`share.tunnelName` — all in one step, no browser, no login, nothing written outside CaseClerk's own data directory. `caseclerk share start` then runs cloudflared against that `config.yml` instead of the login-dependent `--url` form. Add `--tunnel-name <name>` if it isn't `caseclerk`.

### Add the connector in ChatGPT (either option)

```sh
caseclerk share start
```

This prints the public URL (`https://caseclerk.yourdomain.com/mcp`). In ChatGPT: **Settings → Apps & Connectors → Advanced settings → Developer mode**, then create a connector with that URL and **OAuth** authentication. ChatGPT registers itself automatically (dynamic client registration) and completes an authorization-code + PKCE exchange against CaseClerk's own embedded authorization server — there's no login screen to click through on CaseClerk's side; the security boundary is the tunnel itself being off by default.

**(Optional, Windows only) Create toggle shortcuts:**

```sh
caseclerk share shortcuts
```

Drops "CaseClerk Sharing ON.lnk" / "CaseClerk Sharing OFF.lnk" on the Desktop, each running `share start`/`share stop` with a double-click — the day-to-day interface for a non-technical user (see below).

### Day to day

```sh
caseclerk share start    # brings up the HTTP server + tunnel
caseclerk share status   # running state, public URL, last 10 tool calls
caseclerk share stop     # tears both down
caseclerk audit          # the full remote-request audit log, any time
```

The "CaseClerk Sharing ON/OFF" Desktop shortcuts from `share shortcuts` above cover the same two commands with a double-click — no terminal required once the one-time setup is done.

### Security posture

- **Off by default.** Nothing listens beyond localhost until `share start` is run, and it's meant to be stopped when not in use.
- **Localhost bind, always.** The HTTP transport binds `127.0.0.1` only; there is no `--host` flag to change that.
- **Outbound-only tunnel.** cloudflared makes an outbound connection to Cloudflare's edge — no inbound port is ever opened on your router or firewall.
- **OAuth on every request.** Every tool call over HTTP requires a valid bearer token issued through the authorization-code + PKCE flow; a missing or invalid token gets a 401 with `WWW-Authenticate` before any tool code runs.
- **Audit trail.** Every HTTP-transport tool call (tool name, arguments summary, success/failure) is logged to the same SQLite db, readable via `caseclerk audit` or `caseclerk share status`. stdio never writes to this log.

### Installing on the attorney's machine

The attorney's daily driver is the ChatGPT app, not Claude Desktop, so his machine doesn't need Python, uv, or a terminal habit — just the packaged Windows build. The Cloudflare-account steps (login, `tunnel create`, DNS route — Option A above, steps 1–2) happen ahead of time, at home, on the developer's own machine, which produces a tunnel credentials JSON file; the on-site visit itself never touches Cloudflare's login flow:

1. **At home:** run Option A's steps 1–2 above on your own machine, and keep the resulting `<tunnel-id>.json` credentials file handy (e.g. on a USB stick).
2. **On site: download and unzip.** Grab `caseclerk-windows-x64.zip` from the [latest release](https://github.com/chrismeyersfsu/caseclerk/releases/latest) and extract it anywhere (e.g. `C:\Users\<name>\CaseClerk\`). The folder is self-contained — `caseclerk.exe` plus everything it needs; nothing else to install.
3. **On site: open a terminal in that folder** (Shift+right-click → "Open PowerShell window here", or `cd` to it) and run the entire non-interactive setup:
   ```powershell
   .\caseclerk.exe init --yes
   .\caseclerk.exe share setup --credentials <path-to-tunnel-id.json> --hostname caseclerk.yourdomain.com
   .\caseclerk.exe share shortcuts
   ```
   That's the whole visit — no browser, no Cloudflare login, nothing typed into a Cloudflare prompt on this machine. `share setup` prints a verification pass at the end confirming the binary, credentials, and config.yml all landed where `share start` expects them.
4. **Start it and add the connector in ChatGPT** — same as "Add the connector in ChatGPT" above, using `.\caseclerk.exe share start`.
5. **Auto-update.** `caseclerk.exe` knows it's a packaged binary (`caseclerk.exe doctor` reports "running as a packaged binary" rather than checking for `uv`) and updates itself accordingly: `caseclerk update` downloads the new release's zip and swaps it into the same folder in place — Windows won't let a running program overwrite its own files, so the swap renames the current ones aside and moves the new ones in, and a leftover-cleanup pass runs at every startup. Nothing to reinstall or re-unzip; just restart `caseclerk.exe` (or a running `share start`) afterward to pick it up. If a swap ever fails (e.g. offline), the command prints the release page URL as a manual fallback instead of leaving a half-updated install.

## Roadmap

Everything in the plan is built. Ideas for later: summarization via a configured endpoint, a cross-case `search_client` tool, `.msg`/`.eml`/`.rtf`/`.xlsx` extractors, and an LLM-in-the-loop e2e workflow.
