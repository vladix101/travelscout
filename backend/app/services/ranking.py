"""Deterministic ranking of eligible trip packages.

Primary key: comparable total cost (known costs + labelled estimates).
Secondary preferences (quality, location, usable time) may move a package by at most
`MAX_BONUS_SHARE` of its cost each, scaled by user weights, so a much cheaper trip
always stays ahead of a marginally nicer one.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.models import BathroomStatus, DataOrigin, RankingPreferences, TripPackage
from app.services.money import q

MAX_BONUS_SHARE = Decimal("0.06")
UNKNOWN_PENALTY_SHARE = Decimal("0.03")


def _clamp(x: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(1), x))


def score_package(pkg: TripPackage, prefs: RankingPreferences) -> tuple[Decimal, dict[str, Decimal]]:
    base = pkg.cost.comparable_total
    offer = pkg.accommodation.offer
    quality = _clamp(((offer.review_score or Decimal("7.5")) - Decimal("7.5")) / Decimal("2.5"))
    location = Decimal(0) if offer.distance_km is None else _clamp(1 - min(offer.distance_km, Decimal(10)) / 10)
    nights = max(pkg.usable_time.nights, 1)
    time_share = _clamp(Decimal(str(pkg.usable_time.usable_hours)) / Decimal(nights * 13))

    parts = {
        "quality": base * MAX_BONUS_SHARE * Decimal(str(prefs.quality_weight)) * quality,
        "location": base * MAX_BONUS_SHARE * Decimal(str(prefs.location_weight)) * location,
        "time": base * MAX_BONUS_SHARE * Decimal(str(prefs.time_weight)) * time_share,
        "unknown_penalty": base * UNKNOWN_PENALTY_SHARE * len(pkg.cost.unknown_items),
    }
    effective = base - parts["quality"] - parts["location"] - parts["time"] + parts["unknown_penalty"]
    return q(effective), parts


def rank_packages(packages: list[TripPackage], prefs: RankingPreferences) -> list[TripPackage]:
    scored: list[tuple[Decimal, Decimal, str, TripPackage, dict[str, Decimal]]] = []
    for p in packages:
        eff, parts = score_package(p, prefs)
        scored.append((eff, p.cost.comparable_total, p.id, p, parts))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    cheapest = min((p.cost.comparable_total for p in packages), default=None)
    out: list[TripPackage] = []
    for i, (eff, _, _, p, parts) in enumerate(scored, start=1):
        out.append(
            p.model_copy(
                update={
                    "rank": i,
                    "rank_score": eff,
                    "highlights": _highlights(p, parts, cheapest, i),
                    "caveats": _caveats(p),
                }
            )
        )
    return out


def _highlights(p: TripPackage, parts: dict[str, Decimal], cheapest: Decimal | None, rank: int) -> list[str]:
    cur = p.cost.currency
    offer = p.accommodation.offer
    h: list[str] = []
    if cheapest is not None and p.cost.comparable_total == cheapest:
        h.append("Najniža uporediva ukupna cena među pronađenim paketima.")
    elif cheapest is not None:
        diff = p.cost.comparable_total - cheapest
        h.append(f"{diff:,.0f} {cur} skuplje od najjeftinijeg paketa.")
    h.append(f"Poznati troškovi: {p.cost.known_total:,.0f} {cur} ({p.cost.per_person_known:,.0f} {cur} po osobi).")
    if offer.review_score is not None:
        h.append(
            f"Ocena smeštaja {offer.review_score}/10"
            + (f" ({offer.review_count} recenzija)." if offer.review_count else ".")
        )
    if offer.distance_km is not None:
        h.append(f"{offer.distance_km} km od {offer.distance_reference or 'centra'}.")
    h.append(f"{p.usable_time.usable_hours:.0f} h za obilazak, {p.usable_time.full_days} punih dana.")
    if rank == 1 and parts["quality"] + parts["location"] + parts["time"] > 0:
        h.append("Sekundarne preferencije (kvalitet, lokacija, vreme) su uzete u obzir u okviru ograničenog opsega.")
    return h


def _caveats(p: TripPackage) -> list[str]:
    c: list[str] = []
    if p.cost.unknown_items:
        c.append("Nepotpuna cena – nepoznato: " + "; ".join(p.cost.unknown_items))
    if not p.flight.fare_is_confirmed_quote:
        c.append("Cena leta je najniža objavljena cena po sedištu, a ne potvrđena ponuda za celu grupu.")
    if p.flight.outbound.arrival_is_estimate or p.flight.inbound.arrival_is_estimate:
        c.append("Vreme sletanja je procena (izvor ne objavljuje vreme dolaska).")
    if p.usable_time.arrival_note:
        c.append(p.usable_time.arrival_note)
    if p.usable_time.departure_note:
        c.append(p.usable_time.departure_note)
    if p.accommodation.offer.bathroom is BathroomStatus.PRIVATE_EXTERNAL:
        c.append("Privatno kupatilo je van sobe.")
    if DataOrigin.DEMO in p.data_origins:
        c.append("DEMO podaci – nisu stvarne ponude.")
    if any(r.is_fallback for r in p.cost.fx_rates):
        c.append("Korišćen je rezervni (ne aktuelni) devizni kurs.")
    return c
