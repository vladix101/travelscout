"""Deterministic accommodation eligibility rules.

An offer is ELIGIBLE only if every mandatory rule is *positively established*.
A rule that cannot be established (missing data) yields NEEDS_VERIFICATION, never a pass.
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.models import (
    PRIVATE_BATHROOM_STATUSES,
    AccommodationOffer,
    BathroomStatus,
    DataOrigin,
    EligibilityResult,
    EligibilityStatus,
    RoomPrivacy,
    SearchConstraints,
)

HOSTEL_PATTERN = re.compile(r"\b(hostel|hostal|hostell|backpacker|dorm|dormitory|capsule)\b", re.I)
SHARED_SLEEPING_PATTERN = re.compile(
    r"\b(dorm(itory)?|bed in|shared room|mixed room|female room|male room|bunk|capsule)\b", re.I
)

RULE_MESSAGES: dict[str, str] = {
    "review_score_missing": "Booking.com ocena gostiju nije poznata.",
    "review_score_below_min": "Ocena gostiju je ispod minimuma.",
    "hostel": "Hosteli su isključeni (i kada nude privatne sobe).",
    "shared_sleeping_space": "Deljeni prostor za spavanje (spavaonica / krevet u sobi).",
    "room_privacy_unknown": "Nije potvrđeno da je soba privatna ili ceo smeštaj.",
    "shared_bathroom": "Kupatilo je zajedničko.",
    "bathroom_unconfirmed": "Privatno kupatilo nije potvrđeno za izabranu sobu.",
    "occupancy_unknown": "Kapacitet sobe nije poznat.",
    "occupancy_insufficient": "Soba nema dovoljan kapacitet za celu grupu.",
    "dates_mismatch": "Datumi boravka se ne poklapaju sa letovima.",
    "availability_unconfirmed": "Dostupnost za tačne datume nije potvrđena.",
    "not_available": "Nije dostupno za tražene datume.",
    "price_unknown": "Ukupna cena boravka nije poznata.",
    "too_far": "Smeštaj je dalje od zadate maksimalne udaljenosti.",
    "distance_unknown": "Udaljenost od centra nije poznata (zadata je maksimalna udaljenost).",
    "source_unverified": "Izvor je samo trag za istraživanje, ne potvrđena ponuda.",
    "rooms_mismatch": "Broj soba u ponudi ne odgovara traženom broju soba.",
}


def evaluate_offer(
    offer: AccommodationOffer,
    constraints: SearchConstraints,
    required_checkin: date,
    required_checkout: date,
) -> EligibilityResult:
    failed: list[str] = []
    unverified: list[str] = []
    extra_messages: list[str] = []

    # 1. Guest review score >= minimum (7.5 floor enforced by SearchConstraints).
    if offer.review_score is None:
        unverified.append("review_score_missing")
    elif offer.review_score < constraints.min_review_score:
        failed.append("review_score_below_min")

    # 2. No hostels / dormitories / shared sleeping spaces.
    type_text = " ".join(filter(None, [offer.property_type, offer.property_name]))
    if HOSTEL_PATTERN.search(type_text):
        failed.append("hostel")
    if offer.room_privacy is RoomPrivacy.SHARED_SPACE or SHARED_SLEEPING_PATTERN.search(offer.room_name or ""):
        failed.append("shared_sleeping_space")
    elif offer.room_privacy is RoomPrivacy.UNKNOWN:
        unverified.append("room_privacy_unknown")

    # 3. Confirmed private bathroom for the selected room/unit.
    if offer.bathroom is BathroomStatus.SHARED:
        failed.append("shared_bathroom")
    elif offer.bathroom not in PRIVATE_BATHROOM_STATUSES:
        unverified.append("bathroom_unconfirmed")
    elif offer.bathroom is BathroomStatus.PRIVATE_EXTERNAL:
        extra_messages.append("Privatno kupatilo je van sobe (nije en-suite).")
    elif offer.bathroom is BathroomStatus.PRIVATE:
        extra_messages.append("Privatno kupatilo; nije navedeno da li je u samoj sobi.")

    # 4. Occupancy for the entire party.
    needed = constraints.party.seated_passengers
    if offer.rooms != constraints.rooms:
        failed.append("rooms_mismatch")
    if offer.max_occupancy is None:
        unverified.append("occupancy_unknown")
    elif offer.max_occupancy * offer.rooms < needed:
        failed.append("occupancy_insufficient")
    if constraints.party.infants:
        extra_messages.append("Bebe: proverite dostupnost krevetca direktno sa objektom.")

    # 5. Exact stay dates and availability.
    if offer.checkin != required_checkin or offer.checkout != required_checkout:
        failed.append("dates_mismatch")
    if offer.available_for_dates is None:
        unverified.append("availability_unconfirmed")
    elif offer.available_for_dates is False:
        failed.append("not_available")

    # 6. Price must be known to be comparable.
    if offer.total_price is None:
        unverified.append("price_unknown")

    # 7. Optional distance limit.
    if constraints.max_distance_km is not None:
        if offer.distance_km is None:
            unverified.append("distance_unknown")
        elif offer.distance_km > constraints.max_distance_km:
            failed.append("too_far")

    # 8. Discovery leads are never confirmed offers.
    if offer.evidence.origin is DataOrigin.UNVERIFIED:
        unverified.append("source_unverified")

    if failed:
        status = EligibilityStatus.INELIGIBLE
    elif unverified:
        status = EligibilityStatus.NEEDS_VERIFICATION
    else:
        status = EligibilityStatus.ELIGIBLE

    messages = [RULE_MESSAGES[c] for c in failed + unverified] + extra_messages
    return EligibilityResult(status=status, failed=failed, unverified=unverified, messages=messages)
