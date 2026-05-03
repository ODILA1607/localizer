# Localizer

Internal tool that aggregates Belgian real estate listings (West- and
Oost-Vlaanderen, postcodes 8xxx + 9xxx) across multiple sources into one
local desktop app. Built by [ODILA](https://www.odila.be) for Vergo.

> Status: **M0 — skeleton bootstrap.** No connectors are wired up yet.

## What it does (when finished)

- Pulls listings from Zimmo, Immoscoop, Immoweb (and any source we add later).
- Stores them in a local SQLite database alongside the `.exe`.
- Shows a single filterable list: postcode, price, bedrooms, surface, EPC, type, condition.
- Each listing links straight to the original advertisement on the source site.
- Lets the user mark listings (`new` / `seen` / `interesting` / `rejected`) and hide rejected ones.
- Refreshes daily on launch, plus a manual "Refresh" button.

Notifications, map view, photo caching, price history dashboards: out of scope for V1.

## Stack

- Python 3.13
- FastAPI + Jinja + HTMX (local web UI on `127.0.0.1`)
- SQLite (single file, local)
- httpx + curl_cffi for scraping
- PyInstaller for distribution as a single `.exe`
- ruff (lint + format), pytest, mypy (strict on `core/` + `connectors/base.py`)

## Layout

```
src/localizer/
  core/           canonical model, storage, dedup, runner — mypy strict
  connectors/     one module per source, plus the shared base.py contract
  ui/             FastAPI server + templates + static assets
  config.py       hardcoded scope (postcodes), HTTP defaults
  main.py         entry point: `localizer` console script
tests/
  fixtures/       saved HTML responses per source — every parser test runs offline
```

## Development

Requires Python 3.13.

```powershell
# create venv + install dev deps in editable mode
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# run tests
pytest

# lint + format
ruff check .
ruff format .

# type-check the strict-typed core
mypy
```

## Distribution

Built `.exe` is uploaded to OneDrive; download link shared with Thomas
via WhatsApp / e-mail. GitHub Releases under the private
`ODILA1607/localizer` repo are the canonical record of every version.

## Roadmap

| Milestone | Inhoud |
|-----------|--------|
| M0 | Repo bootstrap (this commit) |
| M1 | Canonical model + SQLite + dedup + tests |
| M2 | Connector base + 3 stub connectors with fixtures |
| M3 | Runner + `localizer refresh` CLI |
| M4 | UI: list view, filters, refresh button, status marking |
| M5 | PyInstaller build + `build-release.yml` workflow |
| **V1 — week 2 doel** | Zimmo + Immoscoop end-to-end, lokale `.exe` voor Thomas |
| V1.1 | Immoweb (Cloudflare-werk), update-check, canary, "Stuur logs" |

See the brein concept page `pand-aggregator-skelet` for the full design.
