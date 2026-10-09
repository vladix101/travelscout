import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
import respx

from app.db import Database
from app.domain.models import BathroomStatus, DataOrigin, Party, RoomPrivacy
from app.providers.base import DestinationInfo, ProviderBlockedError
from app.providers.booking_parse import (
    classify_room,
    clean_property_url,
    looks_like_captcha,
    parse_property_page,
    parse_search_cards,
)
from app.providers.demo import DEMO_PROVIDER, DemoAccommodationProvider, DemoFlightProvider
from app.providers.manual import (
    ManualAccommodationInput,
    ManualAccommodationProvider,
    ManualFlightInput,
    ManualFlightProvider,
)
from app.providers.wizz import WizzLiveProvider, city_name, parse_timetable_legs, wizz_booking_url
from app.services.money import FxTable
from tests.conftest import make_constraints


@pytest.fixture(autouse=True)
def _no_dns():
    # Unit tests must not depend on DNS; the allowlist itself is tested in test_security.py.
    with patch("app.providers.http.assert_url_allowed", lambda url: url):
        yield


# --- Wizz Air ------------------------------------------------------------------------------------


def test_parse_timetable_skips_unpriced_days(fixtures_dir):
    data = json.loads((fixtures_dir / "wizz_timetable_out.json").read_text())
    airports = {
        "BEG": DestinationInfo(iata="BEG", name="Belgrade", lat=44.8184, lon=20.3091, timezone="Europe/Belgrade"),
        "FCO": DestinationInfo(iata="FCO", name="Rome", lat=41.8003, lon=12.2389, timezone="Europe/Rome"),
    }
    legs = parse_timetable_legs(data["outboundFlights"], airports)
    assert len(legs) == 2  # the "checkPrice" day is not a fare
    assert legs[0].fare_per_seat.amount == Decimal("6889.0") and legs[0].fare_per_seat.currency == "RSD"
    assert legs[0].arrival_is_estimate and legs[0].arrival_local is not None
    assert legs[0].flight_number is None


def test_city_name_from_metro_area(fixtures_dir):
    data = json.loads((fixtures_dir / "wizz_map.json").read_text())
    by = {c["iata"]: c for c in data["cities"]}
    assert city_name(by["FCO"], by) == "Rome"
    assert city_name(by["PSA"], by) == "Pisa"


def test_booking_link_format():
    url = wizz_booking_url("BEG", "FCO", date(2026, 11, 4), date(2026, 11, 8), 2, 1, 0)
    assert url == "https://www.wizzair.com/en-gb/booking/select-flight/BEG/FCO/2026-11-04/2026-11-08/2/1/0/null"


def _mock_wizz(router, fixtures_dir, timetable_status=200):
    router.get("https://www.wizzair.com/en-gb").respond(200, text='x apiUrl:"https://be.wizzair.com/29.20.0/Api" x')
    router.get(url__regex=r"https://be\.wizzair\.com/29\.20\.0/Api/asset/map.*").respond(
        200, json=json.loads((fixtures_dir / "wizz_map.json").read_text())
    )
    out = json.loads((fixtures_dir / "wizz_timetable_out.json").read_text())
    inb = json.loads((fixtures_dir / "wizz_timetable_in.json").read_text())

    def timetable(request: httpx.Request) -> httpx.Response:
        if timetable_status != 200:
            return httpx.Response(timetable_status, text="<html>blocked</html>")
        body = json.loads(request.content)
        assert body["priceType"] == "regular"  # never assume Discount Club membership
        first = body["flightList"][0]
        return httpx.Response(200, json=out if first["departureStation"] == "BEG" else inb)

    router.post("https://be.wizzair.com/29.20.0/Api/search/timetable").mock(side_effect=timetable)


@respx.mock
async def test_wizz_provider_builds_round_trips(fixtures_dir):
    _mock_wizz(respx, fixtures_dir)
    fx = FxTable.fallback()
    p = WizzLiveProvider(None, fx)
    dests = await p.list_destinations("BEG", direct_only=True)
    assert [d.iata for d in dests] == ["FCO", "PSA"]  # pseudo-station ROM excluded
    c = make_constraints(
        window_start=date(2026, 11, 3), window_end=date(2026, 11, 6), min_nights=2, max_nights=5, party=Party(adults=2)
    )
    opts = await p.search_round_trips(c, "BEG", dests[0])
    assert opts
    best = opts[0]
    assert best.evidence.origin is DataOrigin.LIVE
    assert best.fare_type.value == "regular"
    assert not best.fare_is_confirmed_quote
    assert best.total_fare_by_leg[0].amount == Decimal("13778.0")  # 6889 RSD x 2 seats
    assert best.total_fare.currency == "EUR"
    assert best.booking_url and best.booking_url.startswith("https://www.wizzair.com/")
    nights = [(o.inbound.departure_local.date() - o.outbound.departure_local.date()).days for o in opts]
    assert all(2 <= n <= 5 for n in nights)


@respx.mock
async def test_wizz_bot_protection_is_reported_not_bypassed(fixtures_dir):
    _mock_wizz(respx, fixtures_dir, timetable_status=429)
    p = WizzLiveProvider(None, FxTable.fallback())
    dest = (await p.list_destinations("BEG", True))[0]
    with pytest.raises(ProviderBlockedError):
        await p.search_round_trips(make_constraints(), "BEG", dest)


@respx.mock
async def test_wizz_results_are_cached(fixtures_dir, tmp_path):
    _mock_wizz(respx, fixtures_dir)
    db = Database(tmp_path / "t.db")
    c = make_constraints(window_start=date(2026, 11, 3), window_end=date(2026, 11, 6))
    p1 = WizzLiveProvider(db, FxTable.fallback())
    dest = (await p1.list_destinations("BEG", True))[0]
    await p1.search_round_trips(c, "BEG", dest)
    p2 = WizzLiveProvider(db, FxTable.fallback())
    opts = await p2.search_round_trips(c, "BEG", dest)
    assert opts[0].evidence.origin is DataOrigin.CACHED
    db.close()


# --- Booking.com parsing -----------------------------------------------------------------------


def test_parse_search_cards(fixtures_dir):
    cards = parse_search_cards((fixtures_dir / "booking_search.html").read_text(encoding="utf-8"))
    assert [c.name for c in cards] == ["Fixture Apartment", "Fixture Hostel Roma", "Fixture New Place"]
    a = cards[0]
    assert a.review_score == Decimal("8.7") and a.review_count == 1204
    assert a.distance_km == Decimal("1.2") and a.price == Decimal("310") and a.taxes == Decimal("32")
    assert cards[1].distance_km == Decimal("0.8") and cards[1].taxes_included
    assert cards[2].review_score is None


def test_parse_property_page_room_level(fixtures_dir):
    page = parse_property_page((fixtures_dir / "booking_property.html").read_text(encoding="utf-8"))
    assert page.name == "Fixture Guesthouse" and page.property_type == "Guest house"
    assert page.review_score == Decimal("8.1") and page.review_count == 512
    assert page.checkin == date(2026, 11, 4) and page.checkout == date(2026, 11, 8)
    by_block = {b.block_id: b for b in page.blocks}
    assert by_block["1001_1"].total == Decimal("300.0") and by_block["1001_1"].excluded_charges == Decimal("40.0")
    assert by_block["1001_2"].cancellation_text.startswith("Free cancellation")
    assert "Private bathroom" in by_block["1001_1"].facilities
    assert "Shared bathroom" in by_block["1002_1"].facilities
    assert by_block["1003_1"].max_persons == 3


@pytest.mark.parametrize(
    "room,facilities,unit,expected_privacy,expected_bath",
    [
        ("Double Room", ["Private bathroom"], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.PRIVATE),
        ("Double Room", ["Ensuite bathroom"], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.ENSUITE),
        ("Triple Room", ["Private external bathroom"], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.PRIVATE_EXTERNAL),
        ("Double Room with Shared Bathroom", [], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.SHARED),
        ("Double Room", ["Shared bathroom"], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.SHARED),
        ("Bed in 4-Bed Dormitory Room", ["Shared bathroom"], "", RoomPrivacy.SHARED_SPACE, BathroomStatus.SHARED),
        (
            "Apartment",
            ["Entire apartment", "Private bathroom"],
            "",
            RoomPrivacy.ENTIRE_UNIT,
            BathroomStatus.ENTIRE_UNIT,
        ),
        (
            "Apartment",
            [],
            "Entire apartment · 1 bedroom · 1 bathroom",
            RoomPrivacy.ENTIRE_UNIT,
            BathroomStatus.ENTIRE_UNIT,
        ),
        ("Double Room", ["Air conditioning"], "", RoomPrivacy.PRIVATE_ROOM, BathroomStatus.UNKNOWN),
    ],
)
def test_classify_room(room, facilities, unit, expected_privacy, expected_bath):
    privacy, bath, _ = classify_room(room, facilities, unit)
    assert privacy is expected_privacy
    assert bath is expected_bath


def test_property_url_drops_tracking_params():
    url = clean_property_url(
        "https://www.booking.com/hotel/it/x.en-gb.html?aid=1&label=abc", date(2026, 11, 4), date(2026, 11, 8), 2, [7], 1
    )
    assert "label" not in url and "aid" not in url
    assert "checkin=2026-11-04" in url and "age=7" in url


def test_captcha_detection():
    assert looks_like_captcha("<html>Please verify you are human</html>")
    assert not looks_like_captcha('<div data-testid="property-card">captcha in a review</div>')


# --- Demo & manual --------------------------------------------------------------------------------


async def test_demo_data_is_always_labelled_demo():
    fx = FxTable.fallback()
    p = DemoFlightProvider(fx)
    c = make_constraints()
    dest = (await p.list_destinations("BEG", True))[0]
    opts = await p.search_round_trips(c, "BEG", dest)
    assert opts and all(o.evidence.origin is DataOrigin.DEMO for o in opts)
    stays = await DemoAccommodationProvider().search(c, dest, date(2026, 11, 4), date(2026, 11, 8))
    assert stays and all(s.evidence.origin is DataOrigin.DEMO and s.property_name.startswith("DEMO") for s in stays)
    assert all(s.evidence.provider == DEMO_PROVIDER for s in stays)


async def test_manual_imports_are_labelled_manual(tmp_path):
    db = Database(tmp_path / "m.db")
    flight = ManualFlightInput.model_validate(
        {
            "origin": "BEG",
            "destination": "FCO",
            "outbound": {
                "departure_local": "2026-11-04T15:50:00",
                "arrival_local": "2026-11-04T17:25:00",
                "flight_number": "W6 4421",
            },
            "inbound": {"departure_local": "2026-11-08T18:10:00"},
            "price_per_seat_outbound": "55",
            "price_per_seat_inbound": "40",
            "currency": "EUR",
            "baggage_prices": {"checked20": "35"},
        }
    )
    await db.add_manual_offer("flight", flight.model_dump(mode="json"))
    stay = ManualAccommodationInput.model_validate(
        {
            "destination": "FCO",
            "property_name": "My Hotel",
            "property_type": "Hotel",
            "review_score": "8.4",
            "room_name": "Double Room",
            "room_privacy": "private_room",
            "bathroom": "ensuite",
            "max_occupancy": 2,
            "checkin": "2026-11-04",
            "checkout": "2026-11-08",
            "availability_confirmed": True,
            "total_price": "300",
            "fees": [{"name": "City tax", "amount": "16", "included_in_total": False}],
        }
    )
    await db.add_manual_offer("accommodation", stay.model_dump(mode="json"))
    fx = FxTable.fallback()
    fp = ManualFlightProvider(db, fx)
    dest = (await fp.list_destinations("BEG", True))[0]
    assert dest.city == "Rome"
    opts = await fp.search_round_trips(make_constraints(), "BEG", dest)
    assert len(opts) == 1 and opts[0].evidence.origin is DataOrigin.MANUAL
    assert opts[0].outbound.flight_number == "W6 4421" and not opts[0].outbound.arrival_is_estimate
    offers = await ManualAccommodationProvider(db).search(
        make_constraints(), dest, date(2026, 11, 4), date(2026, 11, 8)
    )
    assert offers[0].evidence.origin is DataOrigin.MANUAL
    assert offers[0].total_price.amount == Decimal("316")  # fee not included in the quoted total is added
    db.close()


def test_manual_input_validation():
    with pytest.raises(ValueError):
        ManualAccommodationInput.model_validate(
            {"destination": "FCO", "property_name": "X", "checkin": "2026-11-08", "checkout": "2026-11-04"}
        )
    with pytest.raises(ValueError):
        ManualAccommodationInput.model_validate(
            {
                "destination": "FCO",
                "property_name": "Xy",
                "checkin": "2026-11-04",
                "checkout": "2026-11-08",
                "source_url": "http://evil.example",
            }
        )
    with pytest.raises(ValueError):
        ManualFlightInput.model_validate(
            {
                "origin": "BEG",
                "destination": "FCO",
                "outbound": {"departure_local": "2026-11-08T10:00:00"},
                "inbound": {"departure_local": "2026-11-04T10:00:00"},
                "price_per_seat_outbound": "10",
                "price_per_seat_inbound": "10",
            }
        )
