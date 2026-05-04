# Changelog

All notable changes to Localizer.

## [Unreleased]

### M4 — local web UI
- FastAPI server on 127.0.0.1:8765, server-rendered Jinja templates (no
  SPA), minimal CSS. Browser opens automatically when Localizer.exe is
  run with no args.
- Routes:
  - `GET /` listings + filters (gemeente, postcode, prijs min/max,
    slaapkamers min, opp min, EPC labels, type, staat, bron, text
    search, hide-rejected toggle).
  - `POST /listings/{id}/status` mark a listing as
    nieuw/bekeken/interessant/afgewezen.
  - `POST /refresh` trigger a synchronous refresh from the UI.
- DB layer extended with `query_listings()`, `update_user_status()`,
  `app_meta` table for `last_refresh_at`.
- CLI: `localizer serve [--port N] [--no-browser]`; running with no
  args defaults to `serve` so Thomas just double-clicks the .exe.
- 5 new tests (4 UI smoke tests + 2 CLI argument tests).
- Runtime deps added: fastapi, uvicorn, jinja2, python-multipart.

### Immovlan added (Thomas confirmed source list)
- Thomas confirmed the V1 source list: Immoweb, Zimmo, Immovlan, Immoscoop.
- Added `SourceName.IMMOVLAN`, `ImmovlanConnector`, fixture, 3 tests.
- Same JSON-LD pattern as Zimmo / Immoscoop, sitemap-driven discovery.
- Registered in `ALL_CONNECTORS`; not in `DEFAULT_ENABLED` until live
  discover() validated.

### M3 — runner + CLI + live Zimmo
- `core/dedup.py`: `merge_or_insert()` — inserts a new canonical listing
  or merges a candidate into the existing one with the same fingerprint.
  Existing UUID is preserved as the stable handle. Sources are unioned
  by `(source_name, source_id)`. Null fields on the existing row are
  filled from the candidate; non-null fields are preserved.
- `core/runner.py`: `run_refresh()` orchestrates discover → fetch →
  parse → merge_or_insert per connector. Per-connector statistics
  (`ConnectorResult`) and aggregate `RefreshResult`. Errors per-listing
  do not abort the connector; errors per-connector do not abort the
  rest.
- `main.py`: argparse-driven CLI with `localizer refresh
  [--source X] [-v|-vv]`. Prints database path + per-connector summary.
- `connectors/zimmo.py`: live `discover()` + `fetch()`. Sitemap-driven
  discovery (`/sitemap.xml` → child sitemaps → filter URLs by postcode
  + listing-shape).
- 9 new tests: dedup-merge (insert / merge / fill-nulls / preserve-non-null
  / source-dedup) + runner orchestration with fake connectors (insert /
  merge / per-listing errors / NotImplementedError-safe).

### M2 — connector base + 3 stub connectors with fixtures
- `connectors/base.py`: Connector Protocol (runtime-checkable), shared
  HTTPClient (httpx) with per-host RateLimiter and RobotsCache,
  `ListingRef`/`RawListing` value types, `RobotsBlockedError`.
- `connectors/registry.py`: factory map + DEFAULT_ENABLED (Zimmo only
  for V1 pilot).
- `connectors/_parsing.py`: shared selectolax helpers — `extract_json_ld`,
  `find_jsonld_by_type`, `extract_next_data`.
- Three connector implementations:
  * `ZimmoConnector` — JSON-LD `Residence` parser.
  * `ImmoscoopConnector` — JSON-LD `Apartment`/`House` parser.
  * `ImmowebConnector` — `__NEXT_DATA__` JSON parser with
    `buildingCondition` enum mapping.
- All three implement the Protocol; `discover()`+`fetch()` raise
  NotImplementedError until M3 / V1.1.
- Three synthetic HTML fixtures (8500 Kortrijk / 9000 Gent / 8000 Brugge).
- 19 new tests: per-connector parse + RateLimiter timing + registry.
- Runtime deps: `httpx>=0.27`, `selectolax>=0.3.21`.

### M1 — canonical model + storage + dedup
- `core/models.py`: pydantic `Listing` + `ListingSource` + enums (`SourceName`,
  `ListingStatus`, `PropertyType`, `Condition`, `EpcLabel`, `UserStatus`).
- `core/dedup.py`: address-normalisation + blake2b fingerprint over
  street + postcode + surface + bedrooms.
- `core/db.py`: SQLite schema (`listing`, `listing_source`, `schema_version`),
  WAL journal, foreign keys, idempotent `initialise()`, `upsert_listing()`,
  `find_listing_by_fingerprint()`, `all_listings()`, `mark_source_gone()`.
- 18 new tests (models / dedup / db round-trip + soft-delete).

### M0 — repo bootstrap
- Project layout (`src/localizer` + `tests`).
- `pyproject.toml` with hatchling build backend, ruff + pytest + mypy config.
- Hardcoded postcode scope (8xxx + 9xxx) in `config.py`.
- Smoke tests for package importability, CLI return code, postcode scope.
- `.gitignore`, `LICENSE` (proprietary), `README.md`.
- GitHub Actions: `test.yml` (lint + tests on push/PR).
