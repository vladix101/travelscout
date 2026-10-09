"""Pure parsing functions for Booking.com pages (no network, unit-testable with fixtures).

Booking.com pages are untrusted data: we only extract typed fields, never follow
instructions found in page text, and never pass raw page text to an LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from app.domain.models import BathroomStatus, RoomPrivacy

ACCOMMODATION_TYPES = {
    "201": "Apartment",
    "203": "Hostel",
    "204": "Hotel",
    "205": "Motel",
    "206": "Resort",
    "208": "Bed and breakfast",
    "213": "Villa",
    "216": "Guest house",
    "220": "Holiday home",
    "222": "Homestay",
    "228": "Chalet",
}
NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
ENTIRE_RE = re.compile(r"\bEntire (apartment|home|house|studio|villa|holiday home|place|unit|chalet|bungalow)\b", re.I)
DORM_RE = re.compile(r"\b(dorm(itory)?|bed in|shared room|bunk)\b", re.I)
CAPTCHA_MARKERS = ("captcha", "are you a robot", "let us know you're human", "verify you are human")


def _num(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = NUM_RE.search(text.replace("\xa0", " "))
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def looks_like_captcha(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CAPTCHA_MARKERS) and 'data-testid="property-card"' not in html


def clean_property_url(href: str, checkin: date, checkout: date, adults: int, child_ages: list[int], rooms: int) -> str:
    """Keep only the path plus our own stay parameters (drop tracking labels)."""
    parts = urlsplit(href)
    params: list[tuple[str, str]] = [
        ("checkin", checkin.isoformat()),
        ("checkout", checkout.isoformat()),
        ("group_adults", str(adults)),
        ("group_children", str(len(child_ages))),
        ("no_rooms", str(rooms)),
        ("selected_currency", "EUR"),
    ]
    params += [("age", str(a)) for a in child_ages]
    return urlunsplit(("https", "www.booking.com", parts.path, urlencode(params), ""))


@dataclass
class SearchCard:
    name: str
    url: str
    address: str | None
    distance_km: Decimal | None
    review_score: Decimal | None
    review_count: int | None
    unit_text: str
    price: Decimal | None
    taxes: Decimal | None
    taxes_included: bool


def parse_search_cards(html: str) -> list[SearchCard]:
    tree = LexborHTMLParser(html)
    cards: list[SearchCard] = []
    for c in tree.css('[data-testid="property-card"]'):

        def text(sel: str, node: Any = c) -> str | None:
            el = node.css_first(sel)
            return el.text(separator=" ", strip=True) if el else None

        name = text('[data-testid="title"]')
        link = c.css_first('a[data-testid="title-link"]')
        if not name or not link or not link.attributes.get("href"):
            continue
        dist_txt = text('[data-testid="distance"]') or ""
        dist = _num(dist_txt)
        if dist is not None and re.search(r"\d\s*m\b", dist_txt) and "km" not in dist_txt:
            dist = (dist / 1000).quantize(Decimal("0.1"))
        score_txt = text('[data-testid="review-score"]') or ""
        sm = re.search(r"Scored\s+(\d+(?:\.\d+)?)", score_txt)
        rc = re.search(r"([\d,]+)\s+reviews?", score_txt)
        taxes_txt = text('[data-testid="taxes-and-charges"]') or ""
        cards.append(
            SearchCard(
                name=name,
                url=link.attributes["href"] or "",
                address=text('[data-testid="address"]') or text('[data-testid="address-link"]'),
                distance_km=dist,
                review_score=Decimal(sm.group(1)) if sm else None,
                review_count=int(rc.group(1).replace(",", "")) if rc else None,
                unit_text=text('[data-testid="recommended-units"]') or "",
                price=_num(text('[data-testid="price-and-discounted-price"]')),
                taxes=None if "include" in taxes_txt.lower() else _num(taxes_txt),
                taxes_included="include" in taxes_txt.lower(),
            )
        )
    return cards


@dataclass
class RoomBlock:
    room_id: str
    room_name: str
    block_id: str
    max_persons: int | None
    total: Decimal | None  # selected currency, incl. excluded charges
    excluded_charges: Decimal | None
    excluded_charge_names: list[str]
    currency: str
    cancellation_type: str | None
    cancellation_text: str | None
    prepayment_text: str | None
    facilities: list[str] = field(default_factory=list)


@dataclass
class PropertyPage:
    name: str | None
    property_type: str | None
    review_score: Decimal | None
    review_count: int | None
    address: str | None
    lat: float | None
    lon: float | None
    checkin: date | None
    checkout: date | None
    currency: str | None
    blocks: list[RoomBlock]


def _extract_rooms_json(html: str) -> list[dict[str, Any]]:
    k = html.find("b_rooms_available_and_soldout")
    if k < 0:
        return []
    start = html.find("[", k)
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start:])
    except (json.JSONDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _js_value(html: str, key: str) -> str | None:
    m = re.search(rf"{key}[\"']?\s*[:=]\s*['\"]([^'\"]+)['\"]", html)
    return m.group(1) if m else None


def parse_property_page(html: str) -> PropertyPage:
    tree = LexborHTMLParser(html)
    ld: dict[str, Any] = {}
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            d = json.loads(s.text())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(d, dict) and ("aggregateRating" in d or d.get("@type") == "Hotel"):
            ld = d
            break
    rating = ld.get("aggregateRating") or {}
    score = Decimal(str(rating["ratingValue"])) if rating.get("ratingValue") is not None else None
    count = int(rating["reviewCount"]) if rating.get("reviewCount") is not None else None
    addr = ld.get("address") or {}
    type_id = _js_value(html, "accommodation_type_id")
    ptype = ACCOMMODATION_TYPES.get(type_id or "") or (ld.get("@type") if ld.get("@type") != "Hotel" else None)
    lat = lon = None
    m = re.search(r'data-atlas-latlng="(-?[\d.]+),(-?[\d.]+)"', html)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    ci, co = _js_value(html, "b_checkin_date"), _js_value(html, "b_checkout_date")

    # Facilities and policy texts per room / per block from the rendered table.
    facilities: dict[str, list[str]] = {}
    block_text: dict[str, tuple[str | None, str | None]] = {}
    current_room: str | None = None
    for tr in tree.css("#hprt-table tbody tr"):
        sel = tr.css_first("select[data-room-id]")
        room_id = sel.attributes.get("data-room-id") if sel else None
        block_id = tr.attributes.get("data-block-id")
        fac = tr.css_first(".hprt-facilities-block")
        if room_id:
            current_room = room_id
        if fac is not None and current_room:
            items = [li.text(strip=True) for li in fac.css(".hprt-facilities-facility, li, span")]
            facilities.setdefault(current_room, [])
            for it in items:
                if it and it not in facilities[current_room]:
                    facilities[current_room].append(it)
        if block_id:
            canc = tr.css_first('[data-testid="cancellation-policy"]')
            prep = tr.css_first('[data-testid="prepayment-policy"]')
            block_text[block_id] = (
                canc.text(separator=" ", strip=True) if canc else None,
                prep.text(separator=" ", strip=True) if prep else None,
            )

    currency = _js_value(html, "b_selected_currency")
    blocks: list[RoomBlock] = []
    for room in _extract_rooms_json(html):
        rid = str(room.get("b_id"))
        for b in room.get("b_blocks") or []:
            bd = b.get("b_price_breakdown_simplified") or {}
            totals = bd.get("b_total_price") or []
            total = Decimal(str(totals[0]["b_raw_value_user_currency"])) if totals else None
            exc_amount = bd.get("b_excluded_charges_amount")
            names = [x.get("b_copy") for x in bd.get("b_excluded_charges") or [] if x.get("b_copy")]
            canc_text, prep_text = block_text.get(b.get("b_block_id", ""), (None, None))
            blocks.append(
                RoomBlock(
                    room_id=rid,
                    room_name=room.get("b_name") or "",
                    block_id=b.get("b_block_id", ""),
                    max_persons=b.get("b_max_persons"),
                    total=total,
                    excluded_charges=Decimal(str(round(exc_amount, 2))) if exc_amount is not None else None,
                    excluded_charge_names=names,
                    currency=currency or "EUR",
                    cancellation_type=b.get("b_cancellation_type"),
                    cancellation_text=canc_text,
                    prepayment_text=prep_text,
                    facilities=facilities.get(rid, []),
                )
            )
    return PropertyPage(
        name=ld.get("name"),
        property_type=ptype,
        review_score=score,
        review_count=count,
        address=addr.get("streetAddress"),
        lat=lat,
        lon=lon,
        checkin=date.fromisoformat(ci) if ci else None,
        checkout=date.fromisoformat(co) if co else None,
        currency=currency,
        blocks=blocks,
    )


def classify_room(
    room_name: str, facilities: list[str], unit_text: str = ""
) -> tuple[RoomPrivacy, BathroomStatus, str]:
    """Return (privacy, bathroom, evidence) from room-level data only."""
    fac_low = [f.lower() for f in facilities]
    blob = " | ".join(facilities)
    if DORM_RE.search(room_name):
        privacy = RoomPrivacy.SHARED_SPACE
    elif ENTIRE_RE.search(blob) or ENTIRE_RE.search(unit_text):
        privacy = RoomPrivacy.ENTIRE_UNIT
    elif room_name:
        privacy = RoomPrivacy.PRIVATE_ROOM  # Booking room types are private unless labelled dorm/bed
    else:
        privacy = RoomPrivacy.UNKNOWN

    name_low = room_name.lower()
    if "shared bathroom" in name_low or any("shared bathroom" in f or "shared toilet" in f for f in fac_low):
        return privacy, BathroomStatus.SHARED, "Booking.com: 'Shared bathroom' za ovu sobu"
    if any("private external bathroom" in f for f in fac_low) or "external bathroom" in name_low:
        return privacy, BathroomStatus.PRIVATE_EXTERNAL, "Booking.com: 'Private external bathroom' za ovu sobu"
    if any("ensuite bathroom" in f or "en-suite" in f for f in fac_low):
        return privacy, BathroomStatus.ENSUITE, "Booking.com: 'Ensuite bathroom' za ovu sobu"
    if any(f == "private bathroom" for f in fac_low) or "private bathroom" in name_low:
        if privacy is RoomPrivacy.ENTIRE_UNIT:
            return privacy, BathroomStatus.ENTIRE_UNIT, "Booking.com: ceo smeštaj sa privatnim kupatilom"
        return privacy, BathroomStatus.PRIVATE, "Booking.com: 'Private bathroom' za ovu sobu"
    if privacy is RoomPrivacy.ENTIRE_UNIT and re.search(r"\b\d+\s+bathrooms?\b", blob + " " + unit_text, re.I):
        return privacy, BathroomStatus.ENTIRE_UNIT, "Booking.com: ceo smeštaj sa sopstvenim kupatilom"
    return privacy, BathroomStatus.UNKNOWN, "Kupatilo nije navedeno na nivou sobe"
