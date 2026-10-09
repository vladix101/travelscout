"""Wizz Air flights via the public endpoints used by wizzair.com.

Wizz Air has no public/partner API. With `WIZZ_LIVE_ENABLED=true` this provider calls two
endpoints that the public website calls without login and without bot challenges:

* ``GET  https://be.wizzair.com/{version}/Api/asset/map``      – route map (airports, connections)
* ``POST https://be.wizzair.com/{version}/Api/search/timetable`` – lowest regular fare per day

The detailed fare search (flight numbers, arrival times, bundles, bag prices) sits behind
Kasada bot protection; we do **not** attempt to bypass it. Consequently live results have:
departure times, lowest regular fare per seat, but *estimated* arrival times, no flight
numbers and unknown bag prices. These are undocumented endpoints and may change or break.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.db import Database
from app.domain.airports import KNOWN_AIRPORTS, timezone_for
from app.domain.models import DataOrigin, Evidence, FlightLeg, FlightOption, Money, SearchConstraints
from app.providers.base import DestinationInfo, ProviderBlockedError, ProviderError
from app.providers.http import BROWSER_UA, ProviderHTTPError, make_client, request_with_retries
from app.services.flights import build_round_trips
from app.services.money import FxTable
from app.services.timeplan import estimate_arrival_local, estimate_block_minutes, great_circle_km

log = logging.getLogger(__name__)

HOME_URL = "https://www.wizzair.com/en-gb"
API_TMPL = "https://be.wizzair.com/{version}/Api"
VERSION_RE = re.compile(r"be\.wizzair\.com/(\d+\.\d+\.\d+)")
MAX_RANGE_DAYS = 30
CHILD_NOTE = "Wizz Air naplaćuje decu kao odrasle (bez dečjeg popusta) – proverite pri rezervaciji."


def wizz_booking_url(
    origin: str, dest: str, out_date: date, ret_date: date, adults: int, children: int, infants: int
) -> str:
    return (
        f"https://www.wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/"
        f"{out_date.isoformat()}/{ret_date.isoformat()}/{adults}/{children}/{infants}/null"
    )


def city_name(city: dict[str, Any], by_iata: dict[str, dict[str, Any]]) -> str:
    """Accommodation search city for an airport ("Rome Fiumicino" -> "Rome")."""
    known = KNOWN_AIRPORTS.get(city["iata"])
    if known:
        return known.city
    mac = city.get("mac")
    if mac and mac in by_iata:
        return re.sub(r"\s*\(All Airports\)\s*$", "", (by_iata[mac].get("shortName") or "").strip())
    name = (city.get("shortName") or city["iata"]).strip()
    name = re.sub(r"\s*\(.*?\)", "", name)  # "Pisa (Tuscany)" -> "Pisa"
    return name.split(" / ")[0].strip()


def parse_timetable_legs(flights: list[dict[str, Any]], airports: dict[str, DestinationInfo]) -> list[FlightLeg]:
    """Convert timetable entries into legs with estimated arrival times."""
    legs: list[FlightLeg] = []
    for f in flights:
        price = f.get("price") or {}
        amount, currency = price.get("amount"), price.get("currencyCode")
        if not amount or not currency or f.get("priceType") != "price":
            continue  # sold out / "check price" / no fare
        o, d = f["departureStation"], f["arrivalStation"]
        oa, da = airports.get(o), airports.get(d)
        o_tz = oa.timezone if oa else timezone_for(o)
        d_tz = da.timezone if da else timezone_for(d)
        block = None
        if oa and da and oa.lat is not None and da.lat is not None and oa.lon is not None and da.lon is not None:
            block = estimate_block_minutes(great_circle_km(oa.lat, oa.lon, da.lat, da.lon))
        for dep in f.get("departureDates") or []:
            dep_local = datetime.fromisoformat(dep)
            arr = estimate_arrival_local(dep_local, o_tz, d_tz, block) if block else None
            legs.append(
                FlightLeg(
                    origin=o,
                    destination=d,
                    departure_local=dep_local,
                    departure_tz=o_tz,
                    arrival_local=arr,
                    arrival_tz=d_tz,
                    arrival_is_estimate=True,
                    flight_number=None,
                    fare_per_seat=Money(amount=Decimal(str(amount)), currency=currency),
                    direct=True,
                )
            )
    return legs


def _ranges(start: date, end: date) -> list[tuple[date, date]]:
    out, cur = [], start
    while cur <= end:
        stop = min(cur + timedelta(days=MAX_RANGE_DAYS - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


class WizzLiveProvider:
    name = "Wizz Air (wizzair.com – javni podaci)"

    def __init__(
        self,
        db: Database | None,
        fx: FxTable,
        *,
        timeout: float = 30.0,
        cache_ttl: int = 3 * 3600,
        concurrency: int = 3,
    ):
        self.db = db
        self.fx = fx
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._sem = asyncio.Semaphore(concurrency)
        self._version: str | None = None
        self._map: dict[str, Any] | None = None
        self._airports: dict[str, DestinationInfo] = {}
        self._lock = asyncio.Lock()
        self.used_cache = False

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": BROWSER_UA,
            "Origin": "https://www.wizzair.com",
            "Referer": "https://www.wizzair.com/",
            "Accept": "application/json, text/plain, */*",
        }

    async def _cached(self, key: str) -> tuple[Any, float] | None:
        return await self.db.cache_get(key) if self.db else None

    async def _store(self, key: str, value: Any, ttl: int) -> None:
        if self.db:
            await self.db.cache_set(key, value, ttl)

    async def _get_version(self) -> str:
        if self._version:
            return self._version
        cached = await self._cached("wizz:version")
        if cached:
            self._version = cached[0]
            return cached[0]
        async with make_client(self.timeout, {"User-Agent": BROWSER_UA}) as c:
            resp = await request_with_retries(c, "GET", HOME_URL)
        m = VERSION_RE.search(resp.text)
        if not m:
            raise ProviderError("Wizz Air: verzija API-ja nije pronađena na javnoj stranici.")
        self._version = m.group(1)
        await self._store("wizz:version", self._version, 6 * 3600)
        return self._version

    async def _load_map(self) -> None:
        async with self._lock:
            if self._map is not None:
                return
            cached = await self._cached("wizz:map")
            if cached:
                self._map = cached[0]
            else:
                version = await self._get_version()
                url = f"{API_TMPL.format(version=version)}/asset/map?languageCode=en-gb"
                async with make_client(self.timeout, self._headers()) as c:
                    resp = await request_with_retries(c, "GET", url)
                data = resp.json()
                self._map = {
                    "cities": [
                        {
                            k: city.get(k)
                            for k in (
                                "iata",
                                "shortName",
                                "countryName",
                                "countryCode",
                                "mac",
                                "isFakeStation",
                                "latitude",
                                "longitude",
                                "connections",
                            )
                        }
                        for city in data.get("cities", [])
                    ]
                }
                await self._store("wizz:map", self._map, 24 * 3600)
            by_iata = {c["iata"]: c for c in self._map["cities"]}
            for city in self._map["cities"]:
                if city.get("isFakeStation"):
                    continue  # "Rome (All Airports)" etc. are search aliases, not airports
                iata = city["iata"]
                known = KNOWN_AIRPORTS.get(iata)
                self._airports[iata] = DestinationInfo(
                    iata=iata,
                    name=(city.get("shortName") or iata).strip(),
                    city=city_name(city, by_iata),
                    country=city.get("countryName"),
                    country_code=city.get("countryCode"),
                    lat=city.get("latitude"),
                    lon=city.get("longitude"),
                    airport_to_centre_km=known.city_center_km if known else None,
                    timezone=timezone_for(iata, city.get("countryCode")),
                )

    async def destination_info(self, iata: str) -> DestinationInfo | None:
        await self._load_map()
        return self._airports.get(iata)

    async def list_destinations(self, origin: str, direct_only: bool) -> list[DestinationInfo]:
        await self._load_map()
        assert self._map is not None
        city = next((c for c in self._map["cities"] if c["iata"] == origin), None)
        if not city:
            return []
        out = []
        for conn in city.get("connections") or []:
            if direct_only and not conn.get("isDirectFlight", True):
                continue
            info = self._airports.get(conn["iata"])
            if info:
                out.append(info)
        return out

    async def _timetable(
        self, origin: str, dest: str, rng: tuple[date, date], constraints: SearchConstraints
    ) -> tuple[list[dict[str, Any]], float | None]:
        """One direction, one date range (<= 30 days). Returns (flights, cache_timestamp|None)."""
        p = constraints.party
        key = f"wizz:tt:{origin}:{dest}:{rng[0]}:{rng[1]}:{p.adults}:{p.children}:{p.infants}"
        cached = await self._cached(key)
        if cached:
            self.used_cache = True
            return cached[0], cached[1]
        version = await self._get_version()
        body = {
            "flightList": [
                {
                    "departureStation": origin,
                    "arrivalStation": dest,
                    "from": rng[0].isoformat(),
                    "to": rng[1].isoformat(),
                }
            ],
            "priceType": "regular",  # never assume Wizz Discount Club membership
            "adultCount": p.adults,
            "childCount": p.children,
            "infantCount": p.infants,
        }
        async with self._sem, make_client(self.timeout, self._headers()) as c:
            try:
                resp = await request_with_retries(
                    c, "POST", f"{API_TMPL.format(version=version)}/search/timetable", json=body
                )
            except ProviderHTTPError as exc:
                if exc.status in (403, 429):
                    raise ProviderBlockedError("Wizz Air je odbio automatski zahtev (zaštita od botova).") from exc
                raise ProviderError(f"Wizz Air timetable: {exc}") from exc
        if "json" not in resp.headers.get("content-type", ""):
            raise ProviderBlockedError("Wizz Air je vratio stranicu za proveru umesto podataka.")
        flights = resp.json().get("outboundFlights") or []
        await self._store(key, flights, self.cache_ttl)
        return flights, None

    async def search_round_trips(
        self, constraints: SearchConstraints, origin: str, destination: DestinationInfo
    ) -> list[FlightOption]:
        await self._load_map()
        d0, d1 = constraints.outbound_dates
        if constraints.date_mode.value == "exact":
            assert constraints.return_date
            in0 = in1 = constraints.return_date
        else:
            in0 = d0 + timedelta(days=max(constraints.min_nights - 1, 0))
            in1 = d1 + timedelta(days=constraints.max_nights + 1)

        jobs = [self._timetable(origin, destination.iata, r, constraints) for r in _ranges(d0, d1)]
        jobs += [self._timetable(destination.iata, origin, r, constraints) for r in _ranges(in0, in1)]
        n_out = len(_ranges(d0, d1))
        results = await asyncio.gather(*jobs)
        outbound: list[FlightLeg] = []
        inbound: list[FlightLeg] = []
        stamps = [ts for _, ts in results if ts]
        for idx, (flights, _) in enumerate(results):
            (outbound if idx < n_out else inbound).extend(parse_timetable_legs(flights, self._airports))
        stored_at = min(stamps) if stamps else None

        retrieved = datetime.fromtimestamp(stored_at, UTC) if stored_at else datetime.now(UTC)
        origin_kind = DataOrigin.CACHED if stored_at else DataOrigin.LIVE
        p = constraints.party

        def evidence(o: FlightLeg, i: FlightLeg) -> Evidence:
            notes = [
                "Najniža redovna cena po sedištu za dan (fare calendar), nije potvrđena ponuda za grupu.",
                "Vreme sletanja je procena na osnovu udaljenosti; broj leta nije dostupan.",
            ]
            return Evidence(
                origin=origin_kind,
                provider="wizzair.com timetable",
                retrieved_at=retrieved,
                source_url=wizz_booking_url(
                    o.origin,
                    o.destination,
                    o.departure_local.date(),
                    i.departure_local.date(),
                    p.adults,
                    p.children,
                    p.infants,
                ),
                notes=notes,
            )

        return build_round_trips(
            constraints,
            outbound,
            inbound,
            id_prefix=f"wz-{destination.iata.lower()}",
            to_eur=lambda m: self.fx.convert(m, "EUR").amount,
            evidence_for=evidence,
            booking_url_for=lambda o, i: wizz_booking_url(
                o.origin,
                o.destination,
                o.departure_local.date(),
                i.departure_local.date(),
                p.adults,
                p.children,
                p.infants,
            ),
            child_note=CHILD_NOTE,
            limit=30,
        )
