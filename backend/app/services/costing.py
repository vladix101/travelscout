"""Deterministic trip cost breakdown.

Four buckets are kept strictly separate:
* mandatory  – fares and accommodation prices that came from a provider/import,
* extra      – requested extras whose price is known (e.g. a priced checked bag),
* estimate   – rough, clearly-labelled planning estimates (transfers, food, activities),
* unknown    – costs we know exist but whose amount we do not know (never treated as zero).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.models import (
    AccommodationOffer,
    CostBreakdown,
    CostLine,
    FlightOption,
    Money,
    SearchConstraints,
)
from app.services.money import FxTable, q

HIGH_COST = {"CH", "NO", "IS", "DK", "GB", "IE", "SE", "FI", "LU", "NL", "FR", "DE", "BE", "AT", "IL", "AE"}
LOW_COST = {
    "RS",
    "BA",
    "MK",
    "AL",
    "ME",
    "BG",
    "RO",
    "HU",
    "PL",
    "GE",
    "AM",
    "TR",
    "UA",
    "MD",
    "XK",
    "EG",
    "MA",
    "KZ",
    "UZ",
}

# Rough planning estimates in EUR per person per day (adult). Not offers; always labelled.
ESTIMATES = {
    "low": {"food": Decimal("25"), "transport": Decimal("4"), "activities": Decimal("10")},
    "mid": {"food": Decimal("35"), "transport": Decimal("7"), "activities": Decimal("15")},
    "high": {"food": Decimal("50"), "transport": Decimal("10"), "activities": Decimal("20")},
}
CHILD_FACTOR = Decimal("0.7")


def cost_tier(country_code: str | None) -> str:
    if country_code in HIGH_COST:
        return "high"
    if country_code in LOW_COST:
        return "low"
    return "mid"


def transfer_estimate_per_person(airport_to_centre_km: float | None) -> Decimal:
    km = Decimal(str(airport_to_centre_km if airport_to_centre_km is not None else 20))
    return min(q(Decimal("5") + km * Decimal("0.13")), Decimal("30"))


def _line(label: str, m: Money | None, category: str, fx: FxTable, cur: str, note: str | None = None) -> CostLine:
    if m is None:
        return CostLine(label=label, amount=None, category="unknown", note=note)
    converted = fx.convert(m, cur)
    if m.currency != cur:
        conv_note = f"{m.amount:,.2f} {m.currency}"
        note = f"{note}; {conv_note}" if note else conv_note
    return CostLine(label=label, amount=converted, category=category, note=note)  # type: ignore[arg-type]


def build_cost_breakdown(
    flight: FlightOption,
    stay: AccommodationOffer,
    constraints: SearchConstraints,
    fx: FxTable,
    *,
    country_code: str | None,
    airport_to_centre_km: float | None,
    trip_days: int,
) -> CostBreakdown:
    cur = constraints.display_currency
    party = constraints.party
    lines: list[CostLine] = []
    unknown: list[str] = []
    currencies: set[str] = set()

    # --- Flights (mandatory) --------------------------------------------------------------
    for label, leg_total in zip(("Let tamo", "Let nazad"), flight.total_fare_by_leg, strict=True):
        currencies.add(leg_total.currency)
        lines.append(_line(f"{label} ({party.seated_passengers} sedišta)", leg_total, "mandatory", fx, cur))
    for p in flight.passengers:
        if p.passenger_type == "infant" and p.count:
            if p.fare_per_person is None:
                unknown.append(f"Taksa za bebe ×{p.count} (po pravcu) – cena nepoznata")
                lines.append(CostLine(label=f"Bebe ×{p.count}", amount=None, category="unknown", note=p.note))
            else:
                currencies.add(p.fare_per_person.currency)
                m = Money(amount=p.fare_per_person.amount * p.count * 2, currency=p.fare_per_person.currency)
                lines.append(_line(f"Bebe ×{p.count} (oba pravca)", m, "mandatory", fx, cur))

    # --- Requested baggage (extra or unknown) ----------------------------------------------
    for bq in flight.baggage_quotes:
        if bq.quantity <= 0:
            continue
        if bq.unit_price is None:
            unknown.append(f"{bq.item} ×{bq.quantity} – cena nepoznata")
            lines.append(CostLine(label=f"{bq.item} ×{bq.quantity}", amount=None, category="unknown", note=bq.note))
        else:
            currencies.add(bq.unit_price.currency)
            m = Money(amount=bq.unit_price.amount * bq.quantity, currency=bq.unit_price.currency)
            lines.append(_line(f"{bq.item} ×{bq.quantity}", m, "extra", fx, cur, bq.note))

    # --- Accommodation (mandatory) ---------------------------------------------------------
    if stay.base_price is not None:
        currencies.add(stay.base_price.currency)
        lines.append(_line(f"Smeštaj, {stay.nights} noći", stay.base_price, "mandatory", fx, cur))
        for fee in stay.fees:
            if not fee.mandatory:
                continue
            if fee.amount is None:
                unknown.append(f"{fee.name} – iznos nepoznat")
                lines.append(CostLine(label=fee.name, amount=None, category="unknown"))
            else:
                currencies.add(fee.amount.currency)
                lines.append(
                    _line(
                        fee.name,
                        fee.amount,
                        "mandatory",
                        fx,
                        cur,
                        "uključeno u cenu" if fee.included_in_total else "plaća se dodatno",
                    )
                )
    elif stay.total_price is not None:
        currencies.add(stay.total_price.currency)
        lines.append(_line(f"Smeštaj, {stay.nights} noći (ukupno)", stay.total_price, "mandatory", fx, cur))
    else:
        unknown.append("Cena smeštaja nepoznata")
        lines.append(CostLine(label="Smeštaj", amount=None, category="unknown"))

    # --- Estimates (clearly labelled) ------------------------------------------------------
    tier = ESTIMATES[cost_tier(country_code)]
    people = Decimal(party.adults) + CHILD_FACTOR * party.children
    transfer_pp = transfer_estimate_per_person(airport_to_centre_km)
    est = [
        ("Transfer aerodrom ↔ smeštaj (procena)", transfer_pp * 2 * people, "javni prevoz / šatl, oba pravca"),
        ("Hrana (procena)", tier["food"] * people * trip_days, f"{trip_days} dana"),
        ("Lokalni prevoz (procena)", tier["transport"] * people * trip_days, None),
        ("Ulaznice i aktivnosti (procena)", tier["activities"] * people * trip_days, "opcione plaćene aktivnosti"),
    ]
    for label, amount, note in est:
        eur = Money(amount=q(amount), currency="EUR")
        lines.append(_line(label, eur, "estimate", fx, cur, note))

    def total(category: str) -> Decimal:
        return q(sum((ln.amount.amount for ln in lines if ln.category == category and ln.amount), Decimal(0)))

    known_mandatory = total("mandatory")
    known_extras = total("extra")
    estimated = total("estimate")
    known_total = known_mandatory + known_extras
    currencies.add(cur)
    return CostBreakdown(
        currency=cur,
        lines=lines,
        known_mandatory=known_mandatory,
        known_extras=known_extras,
        estimated=estimated,
        unknown_items=unknown,
        known_total=known_total,
        comparable_total=known_total + estimated,
        per_person_known=q(known_total / party.seated_passengers),
        is_complete=not unknown,
        fx_rates=fx.used(currencies),
    )


def within_budget(cost: CostBreakdown, constraints: SearchConstraints, fx: FxTable) -> bool:
    """Budget is a hard constraint evaluated on *known* costs only (estimates are excluded)."""
    if constraints.budget_amount is None:
        return True
    budget = fx.convert(Money(amount=constraints.budget_amount, currency=constraints.budget_currency), cost.currency)
    value = cost.per_person_known if constraints.budget_scope.value == "per_person" else cost.known_total
    return value <= budget.amount
