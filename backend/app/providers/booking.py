"""Booking.com accommodation via a real, visible Chromium window (Playwright).

Booking.com's Demand API requires a signed Managed Affiliate Partner contract, so with
`BOOKING_SCRAPER_ENABLED=true` this provider opens booking.com pages the way a person would,
for private, low-volume use. Constraints we keep on purpose:

* no stealth plugins, fingerprint spoofing, proxy rotation or CAPTCHA solving;
* if Booking.com shows a CAPTCHA, a visible window waits for *you* to solve it, a headless
  run fails with a clear "blocked" status;
* small volume: one search page + a few property pages per destination, cached.

Booking.com's markup changes frequently, so parsing can break; failures surface as provider
errors and never as invented offers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlencode

from app.db import Database
from app.domain.models import (
    AccommodationOffer,
    DataOrigin,
    Fee,
    Money,
    RoomPrivacy,
    SearchConstraints,
)
from app.providers.base import DestinationInfo, ProviderBlockedError, ProviderError
from app.providers.booking_parse import (
    SearchCard,
    classify_room,
    clean_property_url,
    looks_like_captcha,
    parse_property_page,
    parse_search_cards,
)
from app.security import assert_url_allowed
from app.services.eligibility import HOSTEL_PATTERN

log = logging.getLogger(__name__)

# Property types requested from Booking.com: apartments, hotels, B&Bs, guest houses, holiday homes.
TYPE_FILTER = ["ht_id=201", "ht_id=204", "ht_id=208", "ht_id=216", "ht_id=220"]
HUMAN_CAPTCHA_WAIT_S = 90


def booking_search_url(
    city: str,
    country: str | None,
    checkin: date,
    checkout: date,
    adults: int,
    child_ages: list[int],
    rooms: int,
    *,
    filtered: bool = True,
) -> str:
    params: list[tuple[str, str]] = [
        ("ss", f"{city}, {country}" if country else city),
        ("checkin", checkin.isoformat()),
        ("checkout", checkout.isoformat()),
        ("group_adults", str(adults)),
        ("group_children", str(len(child_ages))),
        ("no_rooms", str(rooms)),
        ("selected_currency", "EUR"),
        ("lang", "en-gb"),
    ]
    params += [("age", str(a)) for a in child_ages]
    if filtered:
        params += [("order", "price"), ("nflt", ";".join(["review_score=70", *TYPE_FILTER]))]
    return "https://www.booking.com/searchresults.en-gb.html?" + urlencode(params)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"[^\w\s.,:;€£$%()\-/'’]", " ", text)
    return re.sub(r"\s+", " ", text).strip() or None


CANCELLATION_LABELS = {"non_refundable": "Bez povraćaja novca", "free_cancellation": "Besplatno otkazivanje"}


class BookingBrowserProvider:
    name = "Booking.com (pregledač)"

    def __init__(
        self,
        db: Database | None,
        *,
        headless: bool,
        max_properties: int,
        timeout_s: float = 45,
        cache_ttl: int = 3 * 3600,
        concurrency: int = 2,
    ):
        self.db = db
        self.headless = headless
        self.max_properties = max_properties
        self.timeout_ms = int(timeout_s * 1000)
        self.cache_ttl = cache_ttl
        self._sem = asyncio.Semaphore(concurrency)
        self.used_cache = False

    async def search(
        self, constraints: SearchConstraints, destination: DestinationInfo, checkin: date, checkout: date
    ) -> list[AccommodationOffer]:
        p = constraints.party
        key = (
            f"booking:{destination.iata}:{checkin}:{checkout}:{p.adults}:{','.join(map(str, p.child_ages))}"
            f":{constraints.rooms}:{self.max_properties}"
        )
        stored_at: float | None = None
        raw: list[dict] | None = None
        if self.db:
            cached = await self.db.cache_get(key)
            if cached:
                raw, stored_at = cached
                self.used_cache = True
        if raw is None:
            async with self._sem:
                raw = await asyncio.to_thread(self._scrape_sync, constraints, destination, checkin, checkout)
            if self.db:
                await self.db.cache_set(key, raw, self.cache_ttl)
        origin = DataOrigin.CACHED if stored_at else DataOrigin.LIVE
        retrieved = datetime.fromtimestamp(stored_at, UTC) if stored_at else datetime.now(UTC)
        offers = []
        for r in raw:
            r = dict(r)
            r["evidence"] = {**r["evidence"], "origin": origin, "retrieved_at": retrieved}
            offers.append(AccommodationOffer.model_validate(r))
        return offers

    # -- blocking browser work (runs in a worker thread) -------------------------------------
    def _scrape_sync(
        self, constraints: SearchConstraints, dest: DestinationInfo, checkin: date, checkout: date
    ) -> list[dict]:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        p = constraints.party
        url = booking_search_url(
            dest.city or dest.name, dest.country, checkin, checkout, p.adults, p.child_ages, constraints.rooms
        )
        assert_url_allowed(url)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                try:
                    ctx = browser.new_context(locale="en-GB", viewport={"width": 1280, "height": 900})
                    page = ctx.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    # The first visit may run Booking.com's JavaScript challenge, which redirects without
                    # the query string; afterwards the session cookie lets the real search load.
                    page.goto("https://www.booking.com/index.en-gb.html", wait_until="load")
                    page.wait_for_timeout(1500)
                    page.goto(url, wait_until="domcontentloaded")
                    html = self._wait_for(
                        page, '[data-testid="property-card"]', "No properties found", timeout_s=self.timeout_ms / 1000
                    )
                    cards = parse_search_cards(html)
                    candidates = self._preselect(cards, constraints)
                    offers: list[dict] = []
                    for card in candidates[: self.max_properties]:
                        prop_url = clean_property_url(
                            card.url, checkin, checkout, p.adults, p.child_ages, constraints.rooms
                        )
                        assert_url_allowed(prop_url)
                        time.sleep(1.0)  # be polite: low request rate
                        page.goto(prop_url, wait_until="domcontentloaded")
                        prop_html = self._wait_for(page, "#hprt-table", None, timeout_s=15, required=False)
                        offers += self._offers_from_property(
                            prop_html, card, prop_url, dest, constraints, checkin, checkout
                        )
                    return offers
                finally:
                    browser.close()
        except (ProviderBlockedError, ProviderError):
            raise
        except PlaywrightError as exc:
            msg = str(exc).splitlines()[0][:160]
            if "Executable doesn't exist" in msg:
                raise ProviderError(
                    "Playwright Chromium nije instaliran (python -m playwright install chromium)."
                ) from exc
            raise ProviderError(f"Booking.com pregledač: {msg}") from exc

    def _wait_for(
        self,
        page,
        selector: str,
        empty_marker: str | None,
        *,
        timeout_s: float,  # type: ignore[no-untyped-def]
        required: bool = True,
    ) -> str:
        """Wait for `selector`. If a CAPTCHA appears: headless -> fail; visible window -> give the
        person in front of the screen HUMAN_CAPTCHA_WAIT_S seconds to solve it themselves."""
        deadline = time.monotonic() + timeout_s
        extended = False
        while time.monotonic() < deadline:
            if page.locator(selector).count() > 0:
                return page.content()
            html = page.content()
            if empty_marker and empty_marker.lower() in html.lower():
                return html
            if looks_like_captcha(html):
                if self.headless:
                    raise ProviderBlockedError("Booking.com traži CAPTCHA – automatska pretraga je zaustavljena.")
                if not extended:
                    log.warning("Booking.com CAPTCHA shown; waiting for the user to solve it in the window")
                    deadline += HUMAN_CAPTCHA_WAIT_S
                    extended = True
            page.wait_for_timeout(750)
        html = page.content()
        if looks_like_captcha(html):
            raise ProviderBlockedError("Booking.com CAPTCHA nije rešena na vreme.")
        if required:
            title = page.title()
            raise ProviderError(f"Booking.com stranica se nije učitala kako je očekivano ({title[:60]}).")
        return html

    def _preselect(self, cards: list[SearchCard], constraints: SearchConstraints) -> list[SearchCard]:
        """Cheap pre-filter on search cards so only plausible candidates get a detail-page visit."""
        out = []
        for c in cards:
            if c.review_score is not None and c.review_score < constraints.min_review_score:
                continue
            if HOSTEL_PATTERN.search(c.name) or "shared bathroom" in c.unit_text.lower():
                continue
            if c.price is None:
                continue
            out.append(c)
        out.sort(key=lambda c: (c.price or Decimal(0)) + (c.taxes or Decimal(0)))
        return out

    def _offers_from_property(
        self,
        html: str,
        card: SearchCard,
        url: str,
        dest: DestinationInfo,
        constraints: SearchConstraints,
        checkin: date,
        checkout: date,
    ) -> list[dict]:
        page = parse_property_page(html)
        score = page.review_score if page.review_score is not None else card.review_score
        count = page.review_count if page.review_count is not None else card.review_count
        ptype = page.property_type
        if page.blocks == []:
            # Listing without a readable room table: a lead that needs verification, not an offer.
            return [
                self._offer_dict(
                    card,
                    page,
                    url,
                    dest,
                    checkin,
                    checkout,
                    score,
                    count,
                    ptype,
                    room_name=None,
                    privacy=RoomPrivacy.UNKNOWN,
                    bathroom=None,
                    evidence_txt="Tabela soba za tražene datume nije pročitana",
                    max_persons=None,
                    total=None,
                    fees=[],
                    cancellation=None,
                    prepayment=None,
                    available=None,
                    block_id="none",
                    rooms=constraints.rooms,
                )
            ]
        dates_ok = page.checkin == checkin and page.checkout == checkout
        seen_rooms: dict[str, dict] = {}
        for b in page.blocks:
            privacy, bathroom, ev = classify_room(
                b.room_name, b.facilities, card.unit_text if b.room_name and b.room_name in card.unit_text else ""
            )
            total = b.total * constraints.rooms if b.total is not None else None
            fees = []
            if b.excluded_charges:
                fees.append(
                    Fee(
                        name=", ".join(b.excluded_charge_names) or "Takse i naknade",
                        amount=Money(amount=b.excluded_charges * constraints.rooms, currency=b.currency),
                        included_in_total=True,
                    )
                )
            canc = CANCELLATION_LABELS.get(b.cancellation_type or "", None)
            canc_text = _clean(b.cancellation_text)
            d = self._offer_dict(
                card,
                page,
                url,
                dest,
                checkin,
                checkout,
                score,
                count,
                ptype,
                room_name=b.room_name,
                privacy=privacy,
                bathroom=bathroom,
                evidence_txt=ev,
                max_persons=b.max_persons,
                total=total,
                fees=fees,
                cancellation=canc_text or canc,
                prepayment=_clean(b.prepayment_text),
                available=dates_ok,
                block_id=b.block_id,
                rooms=constraints.rooms,
                currency=b.currency,
            )
            # Keep the cheapest rate per room type (other rates differ only in conditions).
            prev = seen_rooms.get(b.room_id)
            if prev is None or (total is not None and Decimal(prev["total_price"]["amount"]) > total):
                seen_rooms[b.room_id] = d
        return list(seen_rooms.values())

    def _offer_dict(
        self,
        card,
        page,
        url,
        dest,
        checkin,
        checkout,
        score,
        count,
        ptype,
        *,
        room_name,  # type: ignore[no-untyped-def]
        privacy,
        bathroom,
        evidence_txt,
        max_persons,
        total,
        fees,
        cancellation,
        prepayment,
        available,
        block_id,
        rooms: int,
        currency: str = "EUR",
    ) -> dict:
        base = None
        if total is not None:
            fee_sum = sum((f.amount.amount for f in fees if f.amount), Decimal(0))
            base = Money(amount=total - fee_sum, currency=currency).model_dump(mode="json")
        notes = ["Cena i uslovi preuzeti sa Booking.com stranice objekta za tačne datume."]
        if page.property_type is None:
            notes.append("Tip objekta nije eksplicitno naveden na stranici.")
        return {
            "id": f"bk-{block_id}",
            "destination": dest.iata,
            "property_name": page.name or card.name,
            "property_type": ptype,
            "location": page.address or card.address,
            "latitude": page.lat,
            "longitude": page.lon,
            "source_url": url,
            "review_score": str(score) if score is not None else None,
            "review_count": count,
            "room_name": room_name,
            "room_privacy": privacy,
            "bathroom": bathroom or "unknown",
            "bathroom_evidence": evidence_txt,
            "max_occupancy": max_persons,
            "rooms": rooms,
            "checkin": checkin.isoformat(),
            "checkout": checkout.isoformat(),
            "available_for_dates": available,
            "base_price": base,
            "fees": [f.model_dump(mode="json") for f in fees],
            "total_price": Money(amount=total, currency=currency).model_dump(mode="json")
            if total is not None
            else None,
            "cancellation": cancellation,
            "prepayment": prepayment,
            "distance_km": str(card.distance_km) if card.distance_km is not None else None,
            "distance_reference": "centra grada (Booking.com)",
            "evidence": {"origin": "live", "provider": "booking.com", "source_url": url, "notes": notes},
        }
