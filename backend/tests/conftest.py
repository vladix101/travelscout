from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

# Tests never use real credentials or live providers unless explicitly marked `live`.
os.environ["OPENAI_API_KEY"] = ""
os.environ["WIZZ_LIVE_ENABLED"] = "false"
os.environ["BOOKING_SCRAPER_ENABLED"] = "false"
os.environ["OSM_ENABLED"] = "false"
os.environ["FX_LIVE_ENABLED"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from app.domain.models import (  # noqa: E402
    AccommodationOffer,
    BathroomStatus,
    DataOrigin,
    Evidence,
    Fee,
    FlightLeg,
    Money,
    Party,
    RoomPrivacy,
    SearchConstraints,
)
from app.services.money import FxTable  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CHECKIN = date(2026, 11, 4)
CHECKOUT = date(2026, 11, 8)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def fx() -> FxTable:
    return FxTable.fallback()


def make_constraints(**kw: Any) -> SearchConstraints:
    base: dict[str, Any] = {
        "date_mode": "flexible",
        "window_start": date(2026, 11, 1),
        "window_end": date(2026, 11, 10),
        "min_nights": 2,
        "max_nights": 5,
    }
    base.update(kw)
    return SearchConstraints(**base)


def make_offer(**kw: Any) -> AccommodationOffer:
    base: dict[str, Any] = dict(
        id="o1",
        destination="FCO",
        property_name="Test Guesthouse",
        property_type="Guest house",
        review_score=Decimal("8.0"),
        review_count=100,
        room_name="Double Room",
        room_privacy=RoomPrivacy.PRIVATE_ROOM,
        bathroom=BathroomStatus.ENSUITE,
        max_occupancy=2,
        rooms=1,
        checkin=CHECKIN,
        checkout=CHECKOUT,
        available_for_dates=True,
        base_price=Money(amount=Decimal("200"), currency="EUR"),
        fees=[Fee(name="City tax", amount=Money(amount=Decimal("24"), currency="EUR"), included_in_total=True)],
        total_price=Money(amount=Decimal("224"), currency="EUR"),
        distance_km=Decimal("1.5"),
        evidence=Evidence(origin=DataOrigin.LIVE, provider="test", retrieved_at=datetime.now(UTC)),
    )
    base.update(kw)
    return AccommodationOffer(**base)


def make_leg(
    origin: str,
    dest: str,
    dep: datetime,
    arr: datetime | None,
    amount: str,
    cur: str = "EUR",
    dep_tz: str | None = "Europe/Belgrade",
    arr_tz: str | None = "Europe/Rome",
) -> FlightLeg:
    return FlightLeg(
        origin=origin,
        destination=dest,
        departure_local=dep,
        departure_tz=dep_tz,
        arrival_local=arr,
        arrival_tz=arr_tz,
        arrival_is_estimate=False,
        fare_per_seat=Money(amount=Decimal(amount), currency=cur),
    )


__all__ = ["make_constraints", "make_offer", "make_leg", "Party", "CHECKIN", "CHECKOUT"]
