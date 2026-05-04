"""Acquisition runner — orchestrates a refresh across enabled connectors.

Pure logic. Iterates the connector list, calls
`discover()` -> `fetch()` -> `parse()` -> `merge_or_insert()` per listing,
collects per-connector statistics. Idempotent: running twice in a row is
safe.

Design rules:
- The runner never touches the network directly; that goes through the
  shared HTTPClient injected into each `discover()` / `fetch()` call.
- Errors in one connector do not abort the rest. Errors per-listing do
  not abort the connector. Everything is logged and counted.
- Successful upserts of source-identical listings degrade to a soft-merge
  with the existing canonical row.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from localizer.config import POSTCODE_RANGES
from localizer.connectors.base import (
    Connector,
    HTTPClient,
    RobotsBlockedError,
)
from localizer.core import db, dedup
from localizer.core.models import SourceName, utc_now

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ConnectorResult:
    """Outcome of one connector's contribution to a refresh."""

    name: SourceName
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    inserted: int = 0
    merged: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RefreshResult:
    """Outcome of a complete refresh across every connector."""

    started_at: datetime
    finished_at: datetime
    per_connector: list[ConnectorResult]

    @property
    def total_inserted(self) -> int:
        return sum(c.inserted for c in self.per_connector)

    @property
    def total_merged(self) -> int:
        return sum(c.merged for c in self.per_connector)

    @property
    def total_errors(self) -> int:
        return sum(len(c.errors) for c in self.per_connector)


# ---------------------------------------------------------------------------
# Postcodes from config
# ---------------------------------------------------------------------------
def _all_postcodes() -> list[int]:
    """Expand `POSTCODE_RANGES` into the explicit integer list."""
    out: list[int] = []
    for low, high in POSTCODE_RANGES:
        out.extend(range(low, high + 1))
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run_refresh(
    *,
    connectors: Iterable[Connector],
    client: HTTPClient,
    db_path: Path,
    postcodes: Iterable[int] | None = None,
    max_per_source: int | None = None,
) -> RefreshResult:
    """Run one refresh pass and return aggregated results.

    `max_per_source` caps the number of listings fetched per connector;
    useful for the first runs against a fresh DB so we don't burn an hour
    crawling thousands of pages before knowing the parser works.
    """
    started = utc_now()
    postcodes_list = list(postcodes) if postcodes is not None else _all_postcodes()

    per: list[ConnectorResult] = []
    db.initialise(db_path)
    with db.connect(db_path) as conn:
        for connector in connectors:
            log.info("Running connector: %s", connector.name)
            per.append(_run_one(connector, client, conn, postcodes_list, max_per_source))
        finished = utc_now()
        db.set_meta(conn, "last_refresh_at", finished.isoformat())

    return RefreshResult(started_at=started, finished_at=finished, per_connector=per)


def _run_one(
    connector: Connector,
    client: HTTPClient,
    conn: object,  # sqlite3.Connection — typed `object` to avoid Protocol leaks
    postcodes: list[int],
    max_listings: int | None,
) -> ConnectorResult:
    result = ConnectorResult(name=connector.name)

    try:
        refs_iter = iter(connector.discover(client, postcodes))
        if max_listings is None:
            refs = list(refs_iter)
        else:
            refs = []
            for ref in refs_iter:
                refs.append(ref)
                if len(refs) >= max_listings:
                    break
    except NotImplementedError:
        result.errors.append("discover() not implemented yet")
        return result
    except RobotsBlockedError as exc:
        result.errors.append(f"discover blocked by robots.txt: {exc.url}")
        return result
    except Exception as exc:
        result.errors.append(f"discover failed: {exc}")
        log.exception("discover failed for %s", connector.name)
        return result

    result.discovered = len(refs)
    log.info("%s: discovered %d candidate listings", connector.name, len(refs))

    for ref in refs:
        try:
            raw = connector.fetch(client, ref)
        except RobotsBlockedError as exc:
            result.errors.append(f"fetch blocked by robots.txt: {exc.url}")
            continue
        except NotImplementedError:
            result.errors.append("fetch() not implemented yet")
            return result
        except Exception as exc:
            result.errors.append(f"fetch {ref.url}: {exc}")
            log.warning("fetch failed for %s: %s", ref.url, exc)
            continue
        result.fetched += 1

        try:
            listing = connector.parse(raw)
        except Exception as exc:
            result.errors.append(f"parse {ref.url}: {exc}")
            log.warning("parse failed for %s: %s", ref.url, exc)
            continue
        result.parsed += 1

        try:
            _, inserted = dedup.merge_or_insert(conn, listing)  # type: ignore[arg-type]
        except Exception as exc:
            result.errors.append(f"upsert {ref.url}: {exc}")
            log.warning("upsert failed for %s: %s", ref.url, exc)
            continue
        if inserted:
            result.inserted += 1
        else:
            result.merged += 1

    log.info(
        "%s: fetched=%d parsed=%d inserted=%d merged=%d errors=%d",
        connector.name,
        result.fetched,
        result.parsed,
        result.inserted,
        result.merged,
        len(result.errors),
    )
    return result
