"""FastAPI app — local web UI on 127.0.0.1.

Server-rendered Jinja pages (no SPA), simple form-based filters.
Routes:
    GET  /                            home / listings (filters via query)
    POST /listings/{id}/status        mark a listing nieuw|bekeken|interessant|afgewezen
    POST /refresh                     trigger a synchronous refresh, then redirect

Designed for one user (Thomas) on his own machine. No auth, no multi-tenant.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from localizer import __version__
from localizer.connectors.base import HTTPClient
from localizer.connectors.registry import DEFAULT_ENABLED, enabled_connectors
from localizer.core import db
from localizer.core.db import default_db_path
from localizer.core.models import (
    Condition,
    EpcLabel,
    PropertyType,
    SourceName,
    UserStatus,
)
from localizer.core.runner import run_refresh

log = logging.getLogger(__name__)


_HERE = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title=f"Localizer {__version__}", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home(
        request: Request,
        gemeente: Annotated[str | None, Query()] = None,
        postcode: Annotated[int | None, Query()] = None,
        prijs_min: Annotated[int | None, Query()] = None,
        prijs_max: Annotated[int | None, Query()] = None,
        slaapkamers_min: Annotated[int | None, Query()] = None,
        opp_min: Annotated[int | None, Query()] = None,
        epc: Annotated[list[str] | None, Query()] = None,
        type_: Annotated[list[str] | None, Query(alias="type")] = None,
        staat: Annotated[list[str] | None, Query()] = None,
        bron: Annotated[list[str] | None, Query()] = None,
        text: Annotated[str | None, Query()] = None,
        verberg_afgewezen: Annotated[bool, Query()] = True,
    ) -> HTMLResponse:
        db.initialise(default_db_path())
        with db.connect(default_db_path()) as conn:
            listings = db.query_listings(
                conn,
                gemeente=gemeente or None,
                postcode=postcode,
                prijs_min=prijs_min,
                prijs_max=prijs_max,
                slaapkamers_min=slaapkamers_min,
                opp_min=opp_min,
                epc_label_in=epc or None,
                type_in=type_ or None,
                staat_in=staat or None,
                source_in=bron or None,
                text=text or None,
                exclude_user_status=[UserStatus.AFGEWEZEN.value] if verberg_afgewezen else None,
                limit=500,
            )
            last_refresh = db.get_meta(conn, "last_refresh_at")
            total_count = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "listings": listings,
                "total_count": total_count,
                "last_refresh": _parse_iso(last_refresh) if last_refresh else None,
                "version": __version__,
                "filters": {
                    "gemeente": gemeente or "",
                    "postcode": postcode,
                    "prijs_min": prijs_min,
                    "prijs_max": prijs_max,
                    "slaapkamers_min": slaapkamers_min,
                    "opp_min": opp_min,
                    "epc": set(epc or []),
                    "type": set(type_ or []),
                    "staat": set(staat or []),
                    "bron": set(bron or []),
                    "text": text or "",
                    "verberg_afgewezen": verberg_afgewezen,
                },
                "options": {
                    "epc": [e.value for e in EpcLabel],
                    "type": [t.value for t in PropertyType],
                    "staat": [s.value for s in Condition],
                    "bron": [s.value for s in SourceName],
                    "user_status": [u.value for u in UserStatus],
                },
            },
        )

    @app.post("/listings/{listing_id}/status")
    def set_status(
        listing_id: str,
        status: Annotated[str, Form()],
        next_url: Annotated[str, Form(alias="next")] = "/",
    ) -> RedirectResponse:
        # Validate against the enum so a tampered request can't insert junk.
        valid = {u.value for u in UserStatus}
        if status not in valid:
            return RedirectResponse("/", status_code=303)
        db.initialise(default_db_path())
        with db.connect(default_db_path()) as conn:
            db.update_user_status(conn, listing_id, status)
        return RedirectResponse(next_url or "/", status_code=303)

    @app.get("/map", response_class=HTMLResponse)
    def map_view(
        request: Request,
        gemeente: Annotated[str | None, Query()] = None,
        postcode: Annotated[int | None, Query()] = None,
        prijs_min: Annotated[int | None, Query()] = None,
        prijs_max: Annotated[int | None, Query()] = None,
        slaapkamers_min: Annotated[int | None, Query()] = None,
        opp_min: Annotated[int | None, Query()] = None,
        epc: Annotated[list[str] | None, Query()] = None,
        type_: Annotated[list[str] | None, Query(alias="type")] = None,
        staat: Annotated[list[str] | None, Query()] = None,
        bron: Annotated[list[str] | None, Query()] = None,
        text: Annotated[str | None, Query()] = None,
        verberg_afgewezen: Annotated[bool, Query()] = True,
    ) -> HTMLResponse:
        db.initialise(default_db_path())
        with db.connect(default_db_path()) as conn:
            listings = db.query_listings(
                conn,
                gemeente=gemeente or None,
                postcode=postcode,
                prijs_min=prijs_min,
                prijs_max=prijs_max,
                slaapkamers_min=slaapkamers_min,
                opp_min=opp_min,
                epc_label_in=epc or None,
                type_in=type_ or None,
                staat_in=staat or None,
                source_in=bron or None,
                text=text or None,
                exclude_user_status=[UserStatus.AFGEWEZEN.value] if verberg_afgewezen else None,
                limit=2000,
            )
        # Build per-postcode centroids from listings that DO have exact
        # lat/lng. Listings without coordinates fall back to their
        # postcode's centroid (with small deterministic jitter so pins
        # don't stack), tagged as approximate. Self-improving: every
        # refresh that brings new lat/lng data widens centroid coverage.
        centroids: dict[int, tuple[float, float]] = {}
        by_postcode: dict[int, list[tuple[float, float]]] = {}
        for listing in listings:
            if listing.lat is not None and listing.lng is not None:
                by_postcode.setdefault(listing.postcode, []).append((listing.lat, listing.lng))
        for pc, coords in by_postcode.items():
            avg_lat = sum(c[0] for c in coords) / len(coords)
            avg_lng = sum(c[1] for c in coords) / len(coords)
            centroids[pc] = (avg_lat, avg_lng)

        with_geo = []
        for listing in listings:
            if listing.lat is not None and listing.lng is not None:
                lat, lng = listing.lat, listing.lng
            else:
                centroid = centroids.get(listing.postcode)
                if centroid is None:
                    continue  # no way to place on the map
                # Deterministic jitter from UUID hash (~50-150m). Keeps
                # multiple listings in the same postcode from stacking
                # but stays within the postcode area.
                listing_hash = hash(str(listing.id))
                jitter_lat = ((listing_hash % 1000) - 500) * 1.5e-5
                jitter_lng = (((listing_hash // 1000) % 1000) - 500) * 2e-5
                lat = centroid[0] + jitter_lat
                lng = centroid[1] + jitter_lng

            with_geo.append(
                {
                    "id": str(listing.id),
                    "lat": lat,
                    "lng": lng,
                    "postcode": listing.postcode,
                    "gemeente": listing.gemeente,
                    "straat": listing.straat,
                    "titel": listing.titel,
                    "prijs_eur": listing.prijs_eur,
                    "epc_label": listing.epc_label.value if listing.epc_label else None,
                    "type": listing.type.value,
                    "user_status": listing.user_status.value,
                    "foto": str(listing.hoofd_foto_url) if listing.hoofd_foto_url else None,
                    "source_url": str(listing.sources[0].source_url) if listing.sources else "",
                    "source_name": (
                        listing.sources[0].source_name.value if listing.sources else ""
                    ),
                }
            )
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="map.html",
            context={
                "version": __version__,
                "pins": with_geo,
                "total_count": len(listings),
                "geo_count": len(with_geo),
                "filters": {
                    "gemeente": gemeente or "",
                    "postcode": postcode,
                    "prijs_min": prijs_min,
                    "prijs_max": prijs_max,
                    "slaapkamers_min": slaapkamers_min,
                    "opp_min": opp_min,
                    "epc": set(epc or []),
                    "type": set(type_ or []),
                    "staat": set(staat or []),
                    "bron": set(bron or []),
                    "text": text or "",
                    "verberg_afgewezen": verberg_afgewezen,
                },
                "options": {
                    "epc": [e.value for e in EpcLabel],
                    "type": [t.value for t in PropertyType],
                    "staat": [s.value for s in Condition],
                    "bron": [s.value for s in SourceName],
                    "user_status": [u.value for u in UserStatus],
                },
            },
        )

    @app.post("/refresh")
    def refresh(
        source: Annotated[str | None, Form()] = None,
        limit: Annotated[int | None, Form()] = None,
        full: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        if source:
            connectors = enabled_connectors(frozenset({SourceName(source)}))
        else:
            connectors = enabled_connectors(DEFAULT_ENABLED)
        if not connectors:
            return RedirectResponse("/", status_code=303)
        with HTTPClient() as client:
            run_refresh(
                connectors=connectors,
                client=client,
                db_path=default_db_path(),
                max_per_source=limit,
                skip_known=full is None,
            )
        return RedirectResponse("/", status_code=303)

    return app


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# Module-level instance — uvicorn picks it up via "localizer.ui.server:app".
app = create_app()
