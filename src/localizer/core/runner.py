"""Acquisition runner — orchestrates a refresh across all enabled connectors.

Filled in M3. Iterates the connector registry, calls `discover()` →
`fetch()` → `parse()` per listing, hands results to the storage layer.
Idempotent: re-running must be safe.
"""

from __future__ import annotations
