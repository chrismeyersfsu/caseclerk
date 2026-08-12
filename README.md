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
   Clio Drive folder on disk
   <clioRoot>/<Client>/<CaseNumber>/**
```

Documents are never sent to the model whole. A background scan walks the Clio Drive folder, converts each document to markdown (`.docx` via `mammoth`, `.pdf` via `pdfminer.six`, `.txt`/`.md` as-is), chunks it, and indexes it into SQLite with full-text search — that index, not the filesystem, is what the MCP tools query. The only write path is `save_email_draft`, which writes a matched `.eml`/`.txt` pair into `<case>/emails-generated/` and nothing else.

## Install

### Claude Desktop

```sh
uv tool install --from git+https://github.com/chrismeyersfsu/caseclerk caseclerk-cli
caseclerk init --write-claude-config
```

`init` discovers your Clio Drive, writes `config.json`, and (with `--write-claude-config`, or when you confirm interactively) merges a `caseclerk` entry into `claude_desktop_config.json`. Restart Claude Desktop afterward.

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
| `caseclerk init [--yes] [--write-claude-config]` | Discover the Clio Drive, write `config.json`, print/write MCP client setup |
| `caseclerk process [--concurrency N]` | Scan `clioRoot` for new/changed documents and drain the processing queue once |
| `caseclerk status` | Show queue/indexed/failed counts and any cached update-available version |
| `caseclerk failures` | List every document currently in the failed state |
| `caseclerk retry <document_id>` / `caseclerk retry --all-failed` | Requeue a document, or every failed document |
| `caseclerk config path` / `get <key>` / `set <key> <value>` | Read or update `config.json` by dotted camelCase key (e.g. `processing.concurrency`) |
| `caseclerk update` | Check GitHub Releases and apply an update on the spot if one is available |
| `caseclerk doctor` | Verify SQLite has FTS5, `uv` is on `PATH`, config is valid, `clioRoot` exists, the db is writable |

During development, run any of these as `uv run caseclerk <command>` from the repo root instead of installing.

## Configuration

Config lives at `platformdirs.user_config_dir("caseclerk")/config.json`. Precedence is `CASECLERK_*` environment variables > the file > built-in defaults.

```json
{
  "clioRoot": "A:\\",
  "emailsFolderName": "emails-generated",
  "emailFileNameTemplate": "{yyyy}-{mm}-{dd}-{slug}",
  "processing": { "concurrency": 2, "watch": true, "ignore": ["emails-generated/**"] },
  "updates": { "auto": true, "checkIntervalHours": 24 },
  "summarization": { "enabled": false, "provider": "anthropic", "baseUrl": "", "apiKeyEnv": "", "model": "" },
  "promptsDir": null
}
```

| Env var | Overrides |
|---|---|
| `CASECLERK_CLIO_ROOT` | `clioRoot` |
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

# build a synthetic Clio Drive to poke at locally
uv run python -m caseclerk_fixtures /tmp/clio-fixture
CASECLERK_CLIO_ROOT=/tmp/clio-fixture uv run caseclerk process
CASECLERK_CLIO_ROOT=/tmp/clio-fixture uv run caseclerk status

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

## Roadmap

Everything above is implemented today, over local stdio only. Remote access for hosts that require a hosted connector (e.g. an HTTPS-reachable tunnel with OAuth, brought up only on demand) is planned but not yet built.
