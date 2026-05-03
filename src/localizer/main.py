"""Entry point for the Localizer desktop app.

Subcommands:
    (no args)            - print version, exit. Used for sanity checks.
    refresh              - run a refresh pass against enabled sources
    refresh --source X   - limit to one source
"""

from __future__ import annotations

import argparse
import logging
import sys

from localizer import __version__
from localizer.connectors.base import HTTPClient
from localizer.connectors.registry import (
    DEFAULT_ENABLED,
    all_connector_names,
    enabled_connectors,
)
from localizer.core.db import default_db_path
from localizer.core.models import SourceName
from localizer.core.runner import RefreshResult, run_refresh


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localizer",
        description="Aggregate Belgian real-estate listings into one local app.",
    )
    parser.add_argument("--version", action="version", version=f"Localizer {__version__}")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="-v info, -vv debug")

    sub = parser.add_subparsers(dest="cmd")

    refresh = sub.add_parser("refresh", help="Run a refresh against enabled sources.")
    refresh.add_argument(
        "--source",
        choices=[s.value for s in all_connector_names()],
        help="Limit to one source (defaults to all enabled).",
    )

    return parser


def _configure_logging(verbose: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbose, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_summary(result: RefreshResult) -> None:
    print()
    print("Refresh summary")
    print("---------------")
    duration = (result.finished_at - result.started_at).total_seconds()
    print(f"Started:  {result.started_at.isoformat()}")
    print(f"Finished: {result.finished_at.isoformat()} ({duration:.1f}s)")
    for cr in result.per_connector:
        print(
            f"  {cr.name.value:<10} discovered={cr.discovered}  fetched={cr.fetched}"
            f"  parsed={cr.parsed}  inserted={cr.inserted}  merged={cr.merged}"
            f"  errors={len(cr.errors)}"
        )
        for err in cr.errors[:5]:
            print(f"    ! {err}")
        if len(cr.errors) > 5:
            print(f"    ... ({len(cr.errors) - 5} more errors suppressed)")
    print(
        f"Totals: inserted={result.total_inserted} merged={result.total_merged} "
        f"errors={result.total_errors}"
    )


def _do_refresh(source_filter: str | None) -> int:
    if source_filter is None:
        connectors = enabled_connectors(DEFAULT_ENABLED)
    else:
        connectors = enabled_connectors(frozenset({SourceName(source_filter)}))

    if not connectors:
        print("No connectors enabled. Nothing to do.", file=sys.stderr)
        return 1

    db_path = default_db_path()
    print(f"Database: {db_path}")
    print(f"Sources:  {', '.join(c.display_name for c in connectors)}")
    print()

    with HTTPClient() as client:
        result = run_refresh(connectors=connectors, client=client, db_path=db_path)

    _print_summary(result)
    return 0 if result.total_errors == 0 else 2


def cli(argv: list[str] | None = None) -> int:
    """Console entry point. Returns process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.cmd is None:
        print(f"Localizer {__version__}")
        print("Run 'localizer refresh' to ingest the latest listings.")
        return 0

    if args.cmd == "refresh":
        return _do_refresh(args.source)

    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable, parser.error exits


if __name__ == "__main__":
    sys.exit(cli())
