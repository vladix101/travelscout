from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.models import (
    BaggageRequirement,
    BudgetScope,
    DataOrigin,
    Evidence,
    Fee,
    FxRate,
    Money,
    Party,
)
from app.services.costing import build_cost_breakdown, within_budget
from app.services.flights import build_round_trips
from app.services.money import CurrencyError, FxTable, money, sum_money
from tests.conftest import make_constraints, make_leg, make_offer


def flight(constraints, fx, *, out="6000", out_cur="RSD", inb="30", in_cur="EUR", bag_prices=None, infant=None):
    o = make_leg("BEG", "FCO", datetime(2026, 11, 4, 15, 50), datetime(2026, 11, 4, 17, 25), out, out_cur)
    i = make_leg(
        "FCO",
        "BEG",
        datetime(2026, 11, 8, 18, 10),
        datetime(2026, 11, 8, 19, 45),
        inb,
        in_cur,
        dep_tz="Europe/Rome",
        arr_tz="Europe/Belgrade",
    )
    opts = build_round_trips(
        constraints,
        [o],
        [i],
        id_prefix="t",
        to_eur=lambda m: fx.convert(m, "EUR").amount,
        evidence_for=lambda a, b: Evidence(origin=DataOrigin.LIVE, provider="test"),
        booking_url_for=lambda a, b: None,
        baggage_price_lookup=(lambda k: bag_prices.get(k)) if bag_prices else None,
        infant_fee=infant,
    )
    assert opts, "round trip should be built"
    return opts[0]


@pytest.fixture
def fx_fixed() -> FxTable:
    t = FxTable()
    t.add(FxRate(base="EUR", quote="RSD", rate=Decimal("117.2"), source="test", as_of=date(2026, 10, 9)))
    t.add(FxRate(base="EUR", quote="GBP", rate=Decimal("0.85"), source="test", as_of=date(2026, 10, 9)))
    return t


def breakdown(c, fx, f, offer=None):
    return build_cost_breakdown(
        f, offer or make_offer(), c, fx, country_code="IT", airport_to_centre_km=30, trip_days=5
    )


def test_fx_conversion_uses_decimal_and_rounds_to_cents(fx_fixed):
    assert fx_fixed.convert(money("117.2", "RSD"), "EUR") == Money(amount=Decimal("1.00"), currency="EUR")
    assert fx_fixed.convert(money("10", "EUR"), "RSD").amount == Decimal("1172.00")
    assert fx_fixed.convert(money("8.5", "GBP"), "EUR").amount == Decimal("10.00")
    assert sum_money([money("117.2", "RSD"), money("1", "EUR")], "EUR", fx_fixed).amount == Decimal("2.00")


def test_unknown_currency_raises(fx_fixed):
    with pytest.raises(CurrencyError):
        fx_fixed.convert(money("1", "JPY"), "EUR")


def test_fallback_rates_are_flagged():
    assert all(r.is_fallback for r in FxTable.fallback().rates.values())


def test_total_cost_flights_times_seats_plus_stay(fx_fixed):
    c = make_constraints(party=Party(adults=2))
    f = flight(c, fx_fixed, out="5860", inb="30")  # 5860 RSD = 50 EUR per seat
    cost = breakdown(c, fx_fixed, f)
    # flights: (50 + 30) * 2 = 160; stay: 200 + 24 city tax = 224
    assert cost.known_mandatory == Decimal("384.00")
    assert cost.known_extras == Decimal("0")
    assert cost.known_total == Decimal("384.00")
    assert cost.per_person_known == Decimal("192.00")
    assert cost.is_complete
    assert {r.quote for r in cost.fx_rates} == {"RSD"}
    assert f.total_fare == Money(amount=Decimal("160.00"), currency="EUR")


def test_mandatory_fees_are_listed_separately(fx_fixed):
    c = make_constraints()
    offer = make_offer(
        base_price=Money(amount=Decimal("180"), currency="EUR"),
        fees=[
            Fee(name="Cleaning fee", amount=Money(amount=Decimal("30"), currency="EUR"), included_in_total=True),
            Fee(name="City tax", amount=Money(amount=Decimal("16"), currency="EUR"), included_in_total=False),
        ],
        total_price=Money(amount=Decimal("226"), currency="EUR"),
    )
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed), offer)
    labels = {ln.label: ln for ln in cost.lines}
    assert labels["Cleaning fee"].amount.amount == Decimal("30.00")
    assert labels["City tax"].category == "mandatory"


def test_unknown_mandatory_fee_is_not_zero(fx_fixed):
    c = make_constraints()
    offer = make_offer(fees=[Fee(name="Resort fee", amount=None, included_in_total=False)])
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed), offer)
    assert not cost.is_complete
    assert any("Resort fee" in u for u in cost.unknown_items)


def test_requested_baggage_with_unknown_price_is_unknown(fx_fixed):
    c = make_constraints(baggage=BaggageRequirement(checked_bags_20kg=1))
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed))
    assert not cost.is_complete
    assert cost.known_extras == Decimal("0")
    assert any("20 kg" in u for u in cost.unknown_items)


def test_requested_baggage_with_known_price_counts_both_directions(fx_fixed):
    c = make_constraints(baggage=BaggageRequirement(checked_bags_20kg=1, cabin_trolley_10kg=2))
    f = flight(c, fx_fixed, bag_prices={"checked20": money("35", "EUR"), "cabin10": money("20", "EUR")})
    cost = breakdown(c, fx_fixed, f)
    # 1 checked bag x 2 directions x 35 + 2 trolleys x 2 directions x 20
    assert cost.known_extras == Decimal("150.00")
    assert cost.is_complete


def test_infant_fee_unknown_is_flagged(fx_fixed):
    c = make_constraints(party=Party(adults=2, infants=1))
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed))
    assert any("bebe" in u.lower() for u in cost.unknown_items)


def test_children_pay_seat_fares(fx_fixed):
    c = make_constraints(party=Party(adults=1, children=2, child_ages=[5, 9]))
    f = flight(c, fx_fixed, out="5860", inb="30")
    assert f.total_fare.amount == Decimal("240.00")  # 3 seats x 80


def test_estimates_are_separate_from_known_total(fx_fixed):
    c = make_constraints()
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed))
    assert cost.estimated > 0
    assert cost.comparable_total == cost.known_total + cost.estimated
    assert all(ln.category == "estimate" for ln in cost.lines if "procena" in ln.label)


def test_display_currency_rsd(fx_fixed):
    c = make_constraints(display_currency="RSD")
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed))
    assert cost.currency == "RSD"
    assert all(ln.amount is None or ln.amount.currency == "RSD" for ln in cost.lines)


def test_budget_uses_known_costs_only(fx_fixed):
    c = make_constraints(party=Party(adults=2), budget_amount=Decimal("200"), budget_scope=BudgetScope.PER_PERSON)
    cost = breakdown(c, fx_fixed, flight(c, fx_fixed, out="5860", inb="30"))  # 192 per person known
    assert within_budget(cost, c, fx_fixed)
    c2 = make_constraints(party=Party(adults=2), budget_amount=Decimal("380"), budget_scope=BudgetScope.GROUP)
    assert not within_budget(cost, c2, fx_fixed)  # group known total 384
    c3 = make_constraints(
        party=Party(adults=2),
        budget_amount=Decimal("23500"),
        budget_currency="RSD",
        budget_scope=BudgetScope.PER_PERSON,
    )
    assert within_budget(cost, c3, fx_fixed)  # 23500 RSD = 200.51 EUR
