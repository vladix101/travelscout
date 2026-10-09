"""OpenStreetMap data for itineraries (documented public APIs, no key).

* Nominatim search (geocoding) – usage policy: max 1 request/s, identifying User-Agent.
* Overpass API – named attractions with wikidata/wikipedia tags near the city centre.

OSM data is community-maintained: opening hours / fees are shown as "OSM data, verify".
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import quote, urlencode

from app.db import Database
from app.domain.models import DataOrigin, PointOfInterest
from app.providers.base import ProviderError
from app.providers.http import make_client, request_with_retries

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"

_nominatim_lock = asyncio.Lock()
_last_call = 0.0


def osm_map_url(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}"


def google_maps_search_url(name: str, lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote(name)}%20{lat:.5f}%2C{lon:.5f}"


def build_overpass_query(lat: float, lon: float, radius_m: int, limit: int) -> str:
    around = f"around:{int(radius_m)},{lat:.6f},{lon:.6f}"
    return (
        "[out:json][timeout:25];("
        f'nwr["tourism"~"^(attraction|museum|viewpoint|gallery)$"]["name"]["wikidata"]({around});'
        f'nwr["historic"~"^(monument|castle|ruins|archaeological_site|memorial|church)$"]["name"]["wikipedia"]({around});'
        f'nwr["leisure"~"^(park|garden)$"]["name"]["wikipedia"]({around});'
        f");out center tags {int(limit)};"
    )


def parse_overpass(data: dict, limit: int) -> list[PointOfInterest]:
    pois: list[PointOfInterest] = []
    seen: set[str] = set()
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        name = tags.get("name:en") or tags.get("name")
        if lat is None or lon is None or not name or name in seen:
            continue
        seen.add(name)
        category = tags.get("tourism") or tags.get("historic") or tags.get("leisure") or tags.get("amenity") or "sight"
        fee = tags.get("fee")
        wiki = tags.get("wikipedia")
        website = tags.get("website") or tags.get("contact:website")
        pois.append(
            PointOfInterest(
                id=f"osm-{el['type'][0]}{el['id']}",
                name=name[:120],
                category=category,
                latitude=float(lat),
                longitude=float(lon),
                opening_hours=(tags.get("opening_hours") or None),
                fee="yes" if fee == "yes" else "no" if fee == "no" else None,
                website=website if website and website.startswith("https://") else None,
                wikipedia=(
                    f"https://{wiki.split(':', 1)[0]}.wikipedia.org/wiki/{quote(wiki.split(':', 1)[1])}"
                    if wiki and ":" in wiki
                    else None
                ),
                map_url=osm_map_url(float(lat), float(lon)),
                source="OpenStreetMap (Overpass API)",
                origin=DataOrigin.LIVE,
            )
        )
    # Prefer entries with Wikipedia articles (a rough proxy for notability), then museums/attractions.
    pois.sort(key=lambda p: (p.wikipedia is None, p.category not in ("attraction", "museum", "viewpoint")))
    return pois[:limit]


class OsmPoiProvider:
    name = "OpenStreetMap (Nominatim + Overpass)"

    def __init__(self, db: Database | None, contact: str = "", timeout: float = 30.0):
        self.db = db
        self.timeout = timeout
        ua = "TravelScout/0.1 (personal trip planner; https://github.com/)"
        self.headers = {"User-Agent": f"{ua} {contact}".strip(), "Accept": "application/json"}

    async def geocode(self, query: str) -> tuple[float, float] | None:
        global _last_call
        key = f"osm:geo:{query.lower()}"
        if self.db and (hit := await self.db.cache_get(key)):
            return tuple(hit[0]) if hit[0] else None  # type: ignore[return-value]
        async with _nominatim_lock:
            wait = 1.1 - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            url = f"{NOMINATIM}?{urlencode({'q': query, 'format': 'jsonv2', 'limit': 1})}"
            async with make_client(self.timeout, self.headers) as c:
                resp = await request_with_retries(c, "GET", url)
            _last_call = time.monotonic()
        data = resp.json()
        result = (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
        if self.db:
            await self.db.cache_set(key, list(result) if result else None, 30 * 86400)
        return result

    async def attractions(self, lat: float, lon: float, radius_m: int = 3000, limit: int = 40) -> list[PointOfInterest]:
        key = f"osm:poi:{lat:.3f}:{lon:.3f}:{radius_m}:{limit}"
        if self.db and (hit := await self.db.cache_get(key)) and hit[0]:
            pois = [PointOfInterest.model_validate(p) for p in hit[0]]
            return [p.model_copy(update={"origin": DataOrigin.CACHED}) for p in pois]
        query = build_overpass_query(lat, lon, radius_m, limit * 2)
        async with make_client(self.timeout + 15, self.headers) as c:
            try:
                resp = await request_with_retries(c, "POST", OVERPASS, data={"data": query})
            except Exception as exc:
                raise ProviderError(f"Overpass API nedostupan: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(
                "Overpass API je vratio neispravan odgovor (verovatno ograničenje broja zahteva)."
            ) from exc
        if data.get("remark") and not data.get("elements"):
            raise ProviderError(f"Overpass API: {str(data['remark'])[:120]}")
        pois = parse_overpass(data, limit)
        if not pois:
            return []  # never cache an empty result
        if self.db:
            await self.db.cache_set(key, [p.model_dump(mode="json") for p in pois], 7 * 86400)
        return pois
