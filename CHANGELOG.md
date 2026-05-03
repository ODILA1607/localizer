# Changelog

All notable changes to Localizer.

## [Unreleased]

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
