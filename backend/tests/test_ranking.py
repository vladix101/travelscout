from datetime import datetime
from decimal import Decimal

from app.domain.models import (
    AssessedOffer,
    CostBreakdown,
    DataOrigin,
    EligibilityResult,
    EligibilityStatus,
    Evidence,
    FlightOption,
    Money,
    PassengerPrice,
    RankingPreferences,
    TripPackage,
    UsableTime,
)
from app.services.ranking import rank_packages
from tests.conftest import make_leg, make_offer


def pkg(pid: str, total: str, score: str = "8.0", dist: str | None = "2", hours: float = 30, unknown: int = 0):
    o = make_leg("BEG", "FCO", datetime(2026, 11, 4, 15, 50), datetime(2026, 11, 4, 17, 25), "40")
    i = make_leg("FCO", "BEG", datetime(2026, 11, 8, 18, 10), datetime(2026, 11, 8, 19, 45), "40")
    flight = FlightOption(
        id=f"f{pid}",
        outbound=o,
        inbound=i,
        passengers=[PassengerPrice(passenger_type="adult", count=2, fare_per_person=None)],
        total_fare=Money(amount=Decimal("160"), currency="EUR"),
        total_fare_by_leg=[Money(amount=Decimal("80"), currency="EUR")] * 2,
        included_baggage="x",
        evidence=Evidence(origin=DataOrigin.DEMO, provider="t"),
    )
    offer = make_offer(id=f"o{pid}", review_score=Decimal(score), distance_km=Decimal(dist) if dist else None)
    t = Decimal(total)
    cost = CostBreakdown(
        currency="EUR",
        lines=[],
        known_mandatory=t,
        known_extras=Decimal(0),
        estimated=Decimal(0),
        unknown_items=[f"u{n}" for n in range(unknown)],
        known_total=t,
        comparable_total=t,
        per_person_known=t / 2,
        is_complete=unknown == 0,
    )
    return TripPackage(
        id=pid,
        destination="FCO",
        destination_name="Rome",
        flight=flight,
        accommodation=AssessedOffer(offer=offer, eligibility=EligibilityResult(status=EligibilityStatus.ELIGIBLE)),
        cost=cost,
        usable_time=UsableTime(nights=4, full_days=3, usable_hours=hours),
        data_origins=[DataOrigin.DEMO],
    )


ZERO = RankingPreferences(quality_weight=0, location_weight=0, time_weight=0)


def test_cheapest_first_without_secondary_preferences():
    ranked = rank_packages([pkg("b", "500"), pkg("a", "400"), pkg("c", "450")], ZERO)
    assert [p.id for p in ranked] == ["a", "c", "b"]
    assert [p.rank for p in ranked] == [1, 2, 3]
    assert "Najniža" in ranked[0].highlights[0]


def test_quality_breaks_near_ties():
    ranked = rank_packages([pkg("low", "400", score="7.6"), pkg("high", "405", score="9.6")], RankingPreferences())
    assert ranked[0].id == "high"


def test_secondary_preferences_cannot_overturn_large_price_gap():
    prefs = RankingPreferences(quality_weight=1, location_weight=1, time_weight=1)
    ranked = rank_packages(
        [pkg("cheap", "400", score="7.5", dist="9", hours=10), pkg("nice", "520", score="10", dist="0", hours=52)],
        prefs,
    )
    assert ranked[0].id == "cheap"


def test_unknown_costs_are_penalised_and_flagged():
    ranked = rank_packages([pkg("incomplete", "400", unknown=2), pkg("complete", "420")], ZERO)
    assert ranked[0].id == "complete"
    assert any("Nepotpuna cena" in c for c in ranked[1].caveats)


def test_demo_packages_carry_demo_caveat():
    ranked = rank_packages([pkg("a", "400")], ZERO)
    assert any("DEMO" in c for c in ranked[0].caveats)


def test_deterministic_order_for_equal_scores():
    ranked = rank_packages([pkg("b", "400"), pkg("a", "400")], ZERO)
    assert [p.id for p in ranked] == ["a", "b"]
