from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import BathroomStatus, DataOrigin, EligibilityStatus, Evidence, Party, RoomPrivacy
from app.services.eligibility import evaluate_offer
from tests.conftest import CHECKIN, CHECKOUT, make_constraints, make_offer


def check(offer, **ckw):
    return evaluate_offer(offer, make_constraints(**ckw), CHECKIN, CHECKOUT)


def test_baseline_offer_is_eligible():
    r = check(make_offer())
    assert r.status is EligibilityStatus.ELIGIBLE, r.messages


@pytest.mark.parametrize("score", ["7.4", "7.49", "5.0"])
def test_rejects_scores_below_7_5(score):
    r = check(make_offer(review_score=Decimal(score)))
    assert r.status is EligibilityStatus.INELIGIBLE
    assert "review_score_below_min" in r.failed


def test_score_exactly_7_5_is_accepted():
    assert check(make_offer(review_score=Decimal("7.5"))).status is EligibilityStatus.ELIGIBLE


def test_missing_score_is_never_eligible():
    r = check(make_offer(review_score=None))
    assert r.status is EligibilityStatus.NEEDS_VERIFICATION
    assert "review_score_missing" in r.unverified


def test_min_score_cannot_be_lowered_below_7_5():
    c = make_constraints(min_review_score=Decimal("6.0"))
    assert c.min_review_score == Decimal("7.5")
    r = evaluate_offer(make_offer(review_score=Decimal("7.0")), c, CHECKIN, CHECKOUT)
    assert r.status is EligibilityStatus.INELIGIBLE


def test_min_score_can_be_raised():
    r = check(make_offer(review_score=Decimal("8.0")), min_review_score=Decimal("8.5"))
    assert "review_score_below_min" in r.failed


@pytest.mark.parametrize(
    "ptype,name", [("Hostel", "Nice Place"), ("Guest house", "Sunny Hostel Rome"), (None, "Backpacker Dorms")]
)
def test_hostels_excluded_even_with_private_room(ptype, name):
    r = check(
        make_offer(
            property_type=ptype,
            property_name=name,
            room_privacy=RoomPrivacy.PRIVATE_ROOM,
            bathroom=BathroomStatus.ENSUITE,
        )
    )
    assert r.status is EligibilityStatus.INELIGIBLE
    assert "hostel" in r.failed


@pytest.mark.parametrize("room", ["Bed in 6-Bed Dormitory Room", "Bunk bed in Mixed Room"])
def test_dormitories_excluded(room):
    r = check(make_offer(room_name=room, property_type="Hotel"))
    assert "shared_sleeping_space" in r.failed


def test_shared_space_privacy_excluded():
    r = check(make_offer(room_privacy=RoomPrivacy.SHARED_SPACE))
    assert "shared_sleeping_space" in r.failed


def test_shared_bathroom_excluded():
    r = check(make_offer(bathroom=BathroomStatus.SHARED))
    assert r.status is EligibilityStatus.INELIGIBLE
    assert "shared_bathroom" in r.failed


def test_unconfirmed_bathroom_needs_verification():
    r = check(make_offer(bathroom=BathroomStatus.UNKNOWN))
    assert r.status is EligibilityStatus.NEEDS_VERIFICATION
    assert "bathroom_unconfirmed" in r.unverified


def test_private_external_bathroom_is_eligible_but_disclosed():
    r = check(make_offer(bathroom=BathroomStatus.PRIVATE_EXTERNAL))
    assert r.status is EligibilityStatus.ELIGIBLE
    assert any("van sobe" in m for m in r.messages)


def test_unknown_room_privacy_needs_verification():
    r = check(make_offer(room_privacy=RoomPrivacy.UNKNOWN))
    assert "room_privacy_unknown" in r.unverified


def test_occupancy_must_fit_whole_party():
    party = Party(adults=2, children=1, child_ages=[8])
    r = check(make_offer(max_occupancy=2), party=party)
    assert "occupancy_insufficient" in r.failed
    assert check(make_offer(max_occupancy=3), party=party).status is EligibilityStatus.ELIGIBLE


def test_occupancy_with_multiple_rooms():
    party = Party(adults=4)
    assert check(make_offer(max_occupancy=2, rooms=2), party=party, rooms=2).status is EligibilityStatus.ELIGIBLE
    assert "rooms_mismatch" in check(make_offer(max_occupancy=2, rooms=1), party=party, rooms=2).failed


def test_unknown_occupancy_needs_verification():
    assert "occupancy_unknown" in check(make_offer(max_occupancy=None)).unverified


def test_exact_stay_dates_required():
    from datetime import timedelta

    r = check(make_offer(checkout=CHECKOUT + timedelta(days=1)))
    assert "dates_mismatch" in r.failed
    r = check(make_offer(checkin=CHECKIN - timedelta(days=1)))
    assert "dates_mismatch" in r.failed


def test_availability_unknown_or_false():
    assert "availability_unconfirmed" in check(make_offer(available_for_dates=None)).unverified
    assert "not_available" in check(make_offer(available_for_dates=False)).failed


def test_missing_price_needs_verification():
    assert "price_unknown" in check(make_offer(total_price=None, base_price=None)).unverified


def test_distance_limit():
    assert "too_far" in check(make_offer(distance_km=Decimal("8")), max_distance_km=Decimal("3")).failed
    assert "distance_unknown" in check(make_offer(distance_km=None), max_distance_km=Decimal("3")).unverified


def test_unverified_source_is_only_a_lead():
    offer = make_offer(evidence=Evidence(origin=DataOrigin.UNVERIFIED, provider="search snippet"))
    assert check(offer).status is EligibilityStatus.NEEDS_VERIFICATION


def test_party_validation():
    with pytest.raises(ValidationError):
        Party(adults=1, children=1, child_ages=[])
    with pytest.raises(ValidationError):
        Party(adults=1, infants=2)
    with pytest.raises(ValidationError):
        Party(adults=2, children=1, child_ages=[1])
