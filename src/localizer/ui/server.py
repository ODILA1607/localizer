"""FastAPI app — local web UI on 127.0.0.1.

Server-rendered Jinja pages (no SPA), simple form-based filters.
Routes:
    GET  /                            home / listings (filters via query)
    POST /listings/{id}/status        mark a listing nieuw|bekeken|interessant|afgewezen
    POST /refresh                     trigger a synchronous refresh, then redirect

Primary deployment is one user (Thomas) on his own machine: no auth, no
multi-tenant. The same `app` also powers a hosted read-only demo (Render)
that flips on three env-gated behaviours — HTTP Basic Auth, a read-only
guard on the mutating routes, and an explicit DB-path override. All three
are inert when their env vars are unset, so desktop UX is untouched.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from localizer import __version__
from localizer.connectors.base import HTTPClient, ListingRef
from localizer.connectors.generic import GenericConnector, source_for_url
from localizer.connectors.registry import (
    ALL_CONNECTORS,
    DEFAULT_ENABLED,
    enabled_connectors,
)
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

# Hosted-demo switch. When set (Render), the mutating routes (/refresh,
# /add-url) are disabled so a live demo can't hang on a network call or
# accidentally mutate the snapshot DB while Thomas is presenting. Unset
# on the desktop → full read/write behaviour.
_READONLY = os.environ.get("LOCALIZER_READONLY") == "1"


class BasicAuthMiddleware:
    """Pure-ASGI HTTP Basic Auth guard.

    Active only when both LOCALIZER_AUTH_USER and LOCALIZER_AUTH_PASS are
    set (the hosted demo). On Thomas' desktop they're unset, so every
    request passes through untouched. Pure ASGI (not BaseHTTPMiddleware)
    so it also covers /static and never buffers response bodies.
    """

    def __init__(self, app: object) -> None:
        self.app = app
        self._user = os.environ.get("LOCALIZER_AUTH_USER") or ""
        self._password = os.environ.get("LOCALIZER_AUTH_PASS") or ""
        self.enabled = bool(self._user and self._password)

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)  # type: ignore[operator]
            return

        header = dict(scope["headers"]).get(b"authorization")
        if header is not None and self._authorised(header):
            await self.app(scope, receive, send)  # type: ignore[operator]
            return

        await send(  # type: ignore[operator]
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", b'Basic realm="Localizer"'),
                    (b"content-type", b"text/plain; charset=utf-8"),
                ],
            }
        )
        await send(  # type: ignore[operator]
            {"type": "http.response.body", "body": b"Authenticatie vereist."}
        )

    def _authorised(self, header: bytes) -> bool:
        try:
            scheme, _, credentials = header.decode("latin-1").partition(" ")
            if scheme.lower() != "basic":
                return False
            user, _, password = base64.b64decode(credentials).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        # Constant-time compare on both fields to avoid timing leaks.
        return secrets.compare_digest(user, self._user) and secrets.compare_digest(
            password, self._password
        )

# Page size for the list view. Small enough that even on a slow laptop
# the page renders in <300ms with full filter set; large enough that
# Thomas isn't paging-clicking constantly.
_LIST_PAGE_SIZE = 50


def _to_int_or_none(value: str | None) -> int | None:
    """Browsers submit blank number inputs as `?x=` (empty string).
    FastAPI's int parser refuses that. We therefore receive the raw
    string and parse it ourselves, returning None on empty / unparseable
    instead of raising 422.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title=f"Localizer {__version__}", docs_url=None, redoc_url=None)
    app.add_middleware(BasicAuthMiddleware)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # Expose the read-only flag to every template so the nav can hide the
    # Refresh / URL-toevoegen actions on the hosted demo without each route
    # having to pass it explicitly.
    _TEMPLATES.env.globals["readonly"] = _READONLY

    @app.get("/", response_class=HTMLResponse)
    def home(
        request: Request,
        gemeente: Annotated[str | None, Query()] = None,
        postcode: Annotated[str | None, Query()] = None,
        prijs_min: Annotated[str | None, Query()] = None,
        prijs_max: Annotated[str | None, Query()] = None,
        slaapkamers_min: Annotated[str | None, Query()] = None,
        opp_min: Annotated[str | None, Query()] = None,
        epc: Annotated[list[str] | None, Query()] = None,
        type_: Annotated[list[str] | None, Query(alias="type")] = None,
        staat: Annotated[list[str] | None, Query()] = None,
        bron: Annotated[list[str] | None, Query()] = None,
        text: Annotated[str | None, Query()] = None,
        verberg_afgewezen: Annotated[bool, Query()] = True,
        page: Annotated[str | None, Query()] = None,
    ) -> HTMLResponse:
        # Parse the int-shaped filters here so blank inputs from the form
        # ('?prijs_min=') become None instead of raising a 422.
        postcode_i = _to_int_or_none(postcode)
        prijs_min_i = _to_int_or_none(prijs_min)
        prijs_max_i = _to_int_or_none(prijs_max)
        slaapkamers_min_i = _to_int_or_none(slaapkamers_min)
        opp_min_i = _to_int_or_none(opp_min)

        # Page param: clamp to >= 1 (will clamp to total_pages once known).
        page_i = max(1, _to_int_or_none(page) or 1)

        # Same shape used by both the COUNT(filtered) and the SELECT,
        # so the result counter and the listings always agree.
        filter_kwargs = {
            "gemeente": gemeente or None,
            "postcode": postcode_i,
            "prijs_min": prijs_min_i,
            "prijs_max": prijs_max_i,
            "slaapkamers_min": slaapkamers_min_i,
            "opp_min": opp_min_i,
            "epc_label_in": epc or None,
            "type_in": type_ or None,
            "staat_in": staat or None,
            "source_in": bron or None,
            "text": text or None,
            "exclude_user_status": (
                [UserStatus.AFGEWEZEN.value] if verberg_afgewezen else None
            ),
        }

        db.initialise(default_db_path())
        with db.connect(default_db_path()) as conn:
            filtered_count = db.count_listings(conn, **filter_kwargs)
            total_pages = max(1, (filtered_count + _LIST_PAGE_SIZE - 1) // _LIST_PAGE_SIZE)
            page_i = min(page_i, total_pages)
            offset = (page_i - 1) * _LIST_PAGE_SIZE
            listings = db.query_listings(
                conn,
                **filter_kwargs,
                limit=_LIST_PAGE_SIZE,
                offset=offset,
            )
            last_refresh = db.get_meta(conn, "last_refresh_at")
            total_count = conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0]

        # Build prev/next URLs preserving every other query parameter.
        prev_url = (
            str(request.url.include_query_params(page=page_i - 1)) if page_i > 1 else None
        )
        next_url = (
            str(request.url.include_query_params(page=page_i + 1))
            if page_i < total_pages
            else None
        )

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "listings": listings,
                "total_count": total_count,
                "filtered_count": filtered_count,
                "page": page_i,
                "total_pages": total_pages,
                "page_size": _LIST_PAGE_SIZE,
                "page_first": offset + 1 if filtered_count > 0 else 0,
                "page_last": min(offset + _LIST_PAGE_SIZE, filtered_count),
                "prev_url": prev_url,
                "next_url": next_url,
                "last_refresh": _parse_iso(last_refresh) if last_refresh else None,
                "version": __version__,
                "filters": {
                    "gemeente": gemeente or "",
                    "postcode": postcode_i,
                    "prijs_min": prijs_min_i,
                    "prijs_max": prijs_max_i,
                    "slaapkamers_min": slaapkamers_min_i,
                    "opp_min": opp_min_i,
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
        postcode: Annotated[str | None, Query()] = None,
        prijs_min: Annotated[str | None, Query()] = None,
        prijs_max: Annotated[str | None, Query()] = None,
        slaapkamers_min: Annotated[str | None, Query()] = None,
        opp_min: Annotated[str | None, Query()] = None,
        epc: Annotated[list[str] | None, Query()] = None,
        type_: Annotated[list[str] | None, Query(alias="type")] = None,
        staat: Annotated[list[str] | None, Query()] = None,
        bron: Annotated[list[str] | None, Query()] = None,
        text: Annotated[str | None, Query()] = None,
        verberg_afgewezen: Annotated[bool, Query()] = True,
    ) -> HTMLResponse:
        postcode_i = _to_int_or_none(postcode)
        prijs_min_i = _to_int_or_none(prijs_min)
        prijs_max_i = _to_int_or_none(prijs_max)
        slaapkamers_min_i = _to_int_or_none(slaapkamers_min)
        opp_min_i = _to_int_or_none(opp_min)

        db.initialise(default_db_path())
        with db.connect(default_db_path()) as conn:
            # Single-query flat lookup — bypasses Pydantic + the N+1
            # source-row SELECT inside `_row_to_listing`. With 26k
            # listings this drops server-side time from ~3-5s to <500ms.
            rows = db.query_listing_pins(
                conn,
                gemeente=gemeente or None,
                postcode=postcode_i,
                prijs_min=prijs_min_i,
                prijs_max=prijs_max_i,
                slaapkamers_min=slaapkamers_min_i,
                opp_min=opp_min_i,
                epc_label_in=epc or None,
                type_in=type_ or None,
                staat_in=staat or None,
                source_in=bron or None,
                text=text or None,
                exclude_user_status=[UserStatus.AFGEWEZEN.value] if verberg_afgewezen else None,
                # Effectively uncapped — Leaflet.markercluster on the
                # client handles 50k+ markers without breaking a sweat.
                limit=100_000,
            )
        # Build per-postcode centroids from listings that DO have exact
        # lat/lng. Listings without coordinates fall back to their
        # postcode's centroid (with small deterministic jitter so pins
        # don't stack), tagged as approximate. Self-improving: every
        # refresh that brings new lat/lng data widens centroid coverage.
        centroids: dict[int, tuple[float, float]] = {}
        by_postcode: dict[int, list[tuple[float, float]]] = {}
        for r in rows:
            lat_v, lng_v = r["lat"], r["lng"]
            if lat_v is not None and lng_v is not None:
                by_postcode.setdefault(r["postcode"], []).append((lat_v, lng_v))
        for pc, coords in by_postcode.items():
            avg_lat = sum(c[0] for c in coords) / len(coords)
            avg_lng = sum(c[1] for c in coords) / len(coords)
            centroids[pc] = (avg_lat, avg_lng)

        with_geo = []
        for r in rows:
            lat_v, lng_v = r["lat"], r["lng"]
            if lat_v is not None and lng_v is not None:
                lat, lng = lat_v, lng_v
            else:
                centroid = centroids.get(r["postcode"])
                if centroid is None:
                    continue  # no way to place on the map
                # Deterministic jitter from id hash (~50-150m). Keeps
                # multiple listings in the same postcode from stacking
                # but stays within the postcode area.
                listing_hash = hash(r["id"])
                jitter_lat = ((listing_hash % 1000) - 500) * 1.5e-5
                jitter_lng = (((listing_hash // 1000) % 1000) - 500) * 2e-5
                lat = centroid[0] + jitter_lat
                lng = centroid[1] + jitter_lng

            with_geo.append(
                {
                    "id": r["id"],
                    "lat": lat,
                    "lng": lng,
                    "postcode": r["postcode"],
                    "gemeente": r["gemeente"],
                    "straat": r["straat"],
                    "titel": r["titel"],
                    "prijs_eur": r["prijs_eur"],
                    "epc_label": r["epc_label"],
                    "type": r["type"],
                    "user_status": r["user_status"],
                    "foto": r["foto"],
                    "source_url": r["source_url"],
                    "source_name": r["source_name"],
                }
            )
        listings = rows  # used only for total_count below
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
                    "postcode": postcode_i,
                    "prijs_min": prijs_min_i,
                    "prijs_max": prijs_max_i,
                    "slaapkamers_min": slaapkamers_min_i,
                    "opp_min": opp_min_i,
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

    @app.get("/add-url", response_class=HTMLResponse)
    def add_url_form(request: Request) -> HTMLResponse:
        """Render the URL-paste form with empty state."""
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="add_url.html",
            context={
                "version": __version__,
                "results": None,
                "urls_input": "",
                "max_urls": 50,
            },
        )

    @app.post("/add-url", response_class=HTMLResponse)
    def add_url_submit(
        request: Request,
        urls: Annotated[str, Form()] = "",
    ) -> HTMLResponse:
        """Process pasted URLs (one per line). For each: pick the right
        connector via `source_for_url`, fetch+parse, then upsert into DB.

        We re-render the same form with per-URL feedback so the user can
        see exactly which URLs landed and which need fixing.
        """
        # Hosted demo: adding URLs means a live fetch+parse, which can hang
        # or fail on stage. Show a friendly notice instead of scraping.
        if _READONLY:
            return _TEMPLATES.TemplateResponse(
                request=request,
                name="add_url.html",
                context={
                    "version": __version__,
                    "results": [
                        {
                            "url": "",
                            "status": "skipped",
                            "message": (
                                "URL toevoegen is uitgeschakeld in de online demo. "
                                "Deze functie werkt in de desktopversie."
                            ),
                            "listing": None,
                        }
                    ],
                    "urls_input": urls,
                    "max_urls": 50,
                },
            )

        raw_urls = [line.strip() for line in urls.splitlines() if line.strip()]
        # Hard cap: prevent the UI from being abused as a sequential
        # crawler. Refresh-runner is the right tool for bulk discovery.
        raw_urls = raw_urls[:50]

        results: list[dict[str, object]] = []
        if raw_urls:
            db.initialise(default_db_path())
            with HTTPClient() as client, db.connect(default_db_path()) as conn:
                for url in raw_urls:
                    source = source_for_url(url)
                    # Browser-required connectors (Zimmo, Immoweb) need
                    # Playwright spin-up — overkill for one-off URL paste.
                    # Tell the user explicitly so they can grab the data
                    # via the sitemap-driven refresh instead.
                    if source in (SourceName.ZIMMO, SourceName.IMMOWEB):
                        results.append(
                            {
                                "url": url,
                                "status": "skipped",
                                "message": (
                                    f"{source.value}: deze bron vereist een echte browser. "
                                    "Gebruik <em>Refresh</em> in de menu-balk om hem mee te scrapen."
                                ),
                                "listing": None,
                            }
                        )
                        continue

                    if source == SourceName.DIVERSEN:
                        connector = GenericConnector()
                    else:
                        connector = ALL_CONNECTORS[source]()

                    try:
                        raw = connector.fetch(client, ListingRef(url=url))
                        listing = connector.parse(raw)
                    except Exception as exc:
                        log.warning("add_url: %s failed: %s", url, exc)
                        results.append(
                            {
                                "url": url,
                                "status": "error",
                                "message": f"{source.value}: kon niet verwerken — {exc}",
                                "listing": None,
                            }
                        )
                        continue

                    # Dedup via fingerprint: if a listing with the same
                    # fingerprint already exists, reuse its UUID + keep
                    # the user's status flag so we don't lose Thomas'
                    # nieuw/bekeken/interessant marking on a re-paste.
                    existing = db.find_listing_by_fingerprint(conn, listing.fingerprint)
                    if existing is not None:
                        listing = listing.model_copy(
                            update={
                                "id": existing.id,
                                "user_status": existing.user_status,
                            }
                        )
                        db.upsert_listing(conn, listing)
                        conn.commit()
                        results.append(
                            {
                                "url": url,
                                "status": "updated",
                                "message": (
                                    f"Al bekend ({source.value}) — opnieuw verwerkt: "
                                    f"{listing.postcode} {listing.gemeente}"
                                ),
                                "listing": listing,
                            }
                        )
                    else:
                        db.upsert_listing(conn, listing)
                        conn.commit()
                        results.append(
                            {
                                "url": url,
                                "status": "added",
                                "message": (
                                    f"Toegevoegd ({source.value}): "
                                    f"{listing.postcode} {listing.gemeente}"
                                    + (f" — {listing.straat}" if listing.straat else "")
                                ),
                                "listing": listing,
                            }
                        )

        return _TEMPLATES.TemplateResponse(
            request=request,
            name="add_url.html",
            context={
                "version": __version__,
                "results": results,
                # Keep pasted text on screen so the user can edit + retry
                # without re-typing the whole list on partial failures.
                "urls_input": urls,
                "max_urls": 50,
            },
        )

    @app.post("/refresh")
    def refresh(
        source: Annotated[str | None, Form()] = None,
        limit: Annotated[int | None, Form()] = None,
        full: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        # Hosted demo: never scrape. A live refresh would block on network
        # calls (and the snapshot DB is read-only by intent), so just bounce
        # back to the listing view.
        if _READONLY:
            return RedirectResponse("/", status_code=303)
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
