# caseclerk-clio-export

Pulls **contacts** and **matters** out of Clio Manage through the official
[Clio API v4](https://docs.developers.clio.com/) — no browser automation.
After a one-time interactive authorization, it runs unattended (Clio access
tokens last 30 days and are auto-refreshed; refresh tokens never expire).

## One-time setup

### 1. Create a Clio developer application

1. Log into Clio, then open <https://app.clio.com/settings/developer_applications>
   (Settings → Developer Applications). Click **Add** / **New Application**.
2. Name it anything (e.g. `clio-export`).
3. Set the **Redirect URI** to exactly:

   ```
   http://127.0.0.1:8788/callback
   ```

4. Grant it read access to Contacts and Matters (read-only is enough).
5. Save, and note the **App Key** (client ID) and **App Secret** (client secret).

### 2. Authorize

```sh
uv run clio-export auth
```

It prompts for the App Key/Secret, opens your browser to Clio's consent page,
catches the redirect on localhost, and stores tokens in
`~/.config/clio-export/credentials.json` (chmod 600). Use `--region eu|ca|au`
if the account is not on the US instance, and `--no-browser` to print the URL
instead of opening a browser.

## Pulling data

```sh
uv run clio-export pull
```

Writes to `./data/` (git-ignored — exports contain client data and must never
be committed):

| File | Contents |
| --- | --- |
| `contacts.json` | Every contact, full nested detail (addresses, emails, phones, custom fields) |
| `matters.json` | Every matter (client, practice area, attorneys, stage, dates, custom fields) |
| `contacts.csv` / `matters.csv` | Flattened one-row-per-record versions for spreadsheets |
| `manifest.json` | Export timestamp and record counts |

Options:

- `--out DIR` — output directory (default `./data`)
- `--snapshot` — write into a timestamped subdirectory instead of overwriting
- `--only contacts|matters` — pull one resource
- `--updated-since 2026-01-01T00:00:00Z` — incremental pull
- `--skip-csv` — JSON only

`clio-export status` shows where credentials live and when the access token
expires.

## Running periodically

Token refresh is automatic, so a cron entry is all you need. Every Monday at
6am, for example:

```
0 6 * * 1 cd /path/to/caseclerk && uv run clio-export pull --snapshot --out ~/clio-exports >> ~/clio-export.log 2>&1
```

## Notes

- Rate limits are respected automatically (waits out HTTP 429 using the
  `Retry-After` header); transient 5xx errors are retried.
- Pagination uses Clio's cursor paging (`order=id(asc)`, 200 records/page),
  so exports are not subject to the 10,000-record offset-paging cap.
- The default field sets live in `src/caseclerk_clio_export/fields.py`; add or
  remove fields there if you need more or less detail.
- `CLIO_EXPORT_CONFIG_DIR` overrides the credentials directory;
  `CLIO_EXPORT_BASE_URL` overrides the API host (used by the e2e test's mock).
