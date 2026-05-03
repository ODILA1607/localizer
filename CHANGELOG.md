# Changelog

All notable changes to Localizer.

## [Unreleased]

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
