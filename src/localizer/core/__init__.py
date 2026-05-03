"""Core domain: canonical model, storage, dedup, runner.

This subpackage is mypy-strict and contains no I/O against external networks
(that lives in `connectors/`). Anything here must be unit-testable offline.
"""
