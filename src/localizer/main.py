"""Entry point for the Localizer desktop app.

Responsibilities (filled in across milestones):
    M0: stub `cli()` that prints version — proves the package is importable.
    M1: initialise SQLite DB on first run.
    M3: hook up runner so `localizer refresh` works end-to-end.
    M4: launch the FastAPI server and open the browser.
    M6: check GitHub Releases for newer version.
"""

from __future__ import annotations

import sys

from localizer import __version__


def cli() -> int:
    """Console entry point. Returns process exit code."""
    print(f"Localizer {__version__}")
    print("M0 skeleton - runtime behaviour will be wired up in later milestones.")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
