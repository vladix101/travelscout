"""Combine one-way legs into round trips that respect the search constraints."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from decimal import Decimal

from app.domain.models import (
    BaggageQuote,
    Evidence,
    FlightLeg,
    FlightOption,
    Money,
    PassengerPrice,
    SearchConstraints,
)
from app.services.money import q
from app.services.timeplan import stay_dates

WIZZ_INCLUDED_BAGGAGE = "1 mali lični predmet (40×30×20 cm) po putniku – osnovna (Basic) tarifa"


def option_id(prefix: str, out: FlightLeg, inb: FlightLeg) -> str:
    raw = f"{out.origin}{out.destination}{out.departure_local.isoformat()}{inb.departure_local.isoformat()}"
    return f"{prefix}-{hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:10]}"


def requested_baggage(
    constraints: SearchConstraints, price_lookup: Callable[[str], Money | None] | None = None
) -> list[BaggageQuote]:
    """Baggage quotes for the requested extras (per direction => quantity x2). Unknown price stays None."""
    out: list[BaggageQuote] = []
    b = constraints.baggage
    for key, label, count in (
        ("cabin10", "Kabinski kofer 10 kg", b.cabin_trolley_10kg),
        ("checked20", "Predati prtljag 20 kg", b.checked_bags_20kg),
        ("checked32", "Predati prtljag 32 kg", b.checked_bags_32kg),
    ):
        if count:
            price = price_lookup(key) if price_lookup else None
            out.append(
                BaggageQuote(
                    item=label,
                    quantity=count * 2,
                    unit_price=price,
                    note="po komadu, po pravcu" if price else "cena zavisi od leta/datuma – proverite kod prevoznika",
                )
            )
    return out


def within_dates(constraints: SearchConstraints, out: FlightLeg, inb: FlightLeg) -> bool:
    d0, d1 = constraints.outbound_dates
    if not (d0 <= out.departure_local.date() <= d1):
        return False
    if constraints.date_mode.value == "exact" and inb.departure_local.date() != constraints.return_date:
        return False
    try:
        checkin, checkout, _ = stay_dates(out, inb)
    except ValueError:
        return False
    nights = (checkout - checkin).days
    return constraints.min_nights <= nights <= constraints.max_nights


def build_round_trips(
    constraints: SearchConstraints,
    outbound: list[FlightLeg],
    inbound: list[FlightLeg],
    *,
    id_prefix: str,
    to_eur: Callable[[Money], Decimal],
    evidence_for: Callable[[FlightLeg, FlightLeg], Evidence],
    booking_url_for: Callable[[FlightLeg, FlightLeg], str | None],
    baggage_price_lookup: Callable[[str], Money | None] | None = None,
    infant_fee: Money | None = None,
    child_note: str | None = None,
    fare_is_confirmed_quote: bool = False,
    included_baggage: str = WIZZ_INCLUDED_BAGGAGE,
    limit: int = 6,
) -> list[FlightOption]:
    seats = constraints.party.seated_passengers
    combos: list[tuple[Decimal, FlightOption]] = []
    for o in outbound:
        for i in inbound:
            if o.destination != i.origin or i.destination != o.origin:
                continue
            if not within_dates(constraints, o, i):
                continue
            legs = [
                Money(amount=o.fare_per_seat.amount * seats, currency=o.fare_per_seat.currency),
                Money(amount=i.fare_per_seat.amount * seats, currency=i.fare_per_seat.currency),
            ]
            passengers = [
                PassengerPrice(
                    passenger_type="adult",
                    count=constraints.party.adults,
                    fare_per_person=None,
                    note="cena po sedištu po pravcu",
                )
            ]
            if constraints.party.children:
                passengers.append(
                    PassengerPrice(
                        passenger_type="child", count=constraints.party.children, fare_per_person=None, note=child_note
                    )
                )
            if constraints.party.infants:
                passengers.append(
                    PassengerPrice(
                        passenger_type="infant",
                        count=constraints.party.infants,
                        fare_per_person=infant_fee,
                        note="taksa za bebu u krilu, po pravcu",
                    )
                )
            total = Money(amount=q(to_eur(legs[0]) + to_eur(legs[1])), currency="EUR")
            opt = FlightOption(
                id=option_id(id_prefix, o, i),
                outbound=o,
                inbound=i,
                passengers=passengers,
                total_fare=total,
                total_fare_by_leg=legs,
                included_baggage=included_baggage,
                baggage_quotes=requested_baggage(constraints, baggage_price_lookup),
                fare_is_confirmed_quote=fare_is_confirmed_quote,
                evidence=evidence_for(o, i),
                booking_url=booking_url_for(o, i),
            )
            combos.append((total.amount, opt))
    combos.sort(key=lambda t: t[0])
    return [c[1] for c in combos[:limit]]
