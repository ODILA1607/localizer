# Changelog

All notable changes to Localizer.

## [Unreleased]

### M0 — repo bootstrap
- Project layout (`src/localizer` + `tests`).
- `pyproject.toml` with hatchling build backend, ruff + pytest + mypy config.
- Hardcoded postcode scope (8xxx + 9xxx) in `config.py`.
- Smoke tests for package importability, CLI return code, postcode scope.
- `.gitignore`, `LICENSE` (proprietary), `README.md`.
- GitHub Actions: `test.yml` (lint + tests on push/PR).
