"""Connector discovery / registry.

Adding a new source = one entry in `ALL_CONNECTORS` plus a module under
`connectors/`. The runner asks `enabled_connectors()` for the list to
iterate over.

Each registry entry is a *factory* (zero-arg callable returning a
`Connector` instance) so the runner can re-instantiate fresh state per
run if it ever needs to.
"""

from __future__ import annotations

from collections.abc import Callable

from localizer.connectors.base import Connector
from localizer.connectors.immoscoop import ImmoscoopConnector
from localizer.connectors.immovlan import ImmovlanConnector
from localizer.connectors.immoweb import ImmowebConnector
from localizer.connectors.zimmo import ZimmoConnector
from localizer.core.models import SourceName

ConnectorFactory = Callable[[], Connector]


ALL_CONNECTORS: dict[SourceName, ConnectorFactory] = {
    SourceName.ZIMMO: ZimmoConnector,
    SourceName.IMMOSCOOP: ImmoscoopConnector,
    SourceName.IMMOVLAN: ImmovlanConnector,
    SourceName.IMMOWEB: ImmowebConnector,
}


# Default enabled set — Immoscoop and Immovlan are reachable with
# curl_cffi's Chrome impersonation (verified via probe). Zimmo and
# Immoweb sit behind Cloudflare's JS-challenge and are deferred to
# V1.1 (Playwright fallback or paid scraping API decision). They
# remain selectable explicitly via `--source zimmo`/`--source immoweb`
# once that escalation lands.
DEFAULT_ENABLED: frozenset[SourceName] = frozenset({SourceName.IMMOSCOOP, SourceName.IMMOVLAN})


def enabled_connectors(
    enabled: frozenset[SourceName] | None = None,
) -> list[Connector]:
    """Instantiate every enabled connector. `None` = `DEFAULT_ENABLED`."""
    selection = enabled if enabled is not None else DEFAULT_ENABLED
    return [ALL_CONNECTORS[name]() for name in selection if name in ALL_CONNECTORS]


def all_connector_names() -> list[SourceName]:
    return list(ALL_CONNECTORS.keys())
