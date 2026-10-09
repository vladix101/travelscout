from decimal import Decimal

from app.agents.coordinator import CoordinatorDraft, merge_draft
from tests.conftest import make_constraints


def test_invalid_group_is_skipped_but_rest_is_applied():
    base = make_constraints()
    draft = CoordinatorDraft(
        destinations=["fco"],
        date_mode="exact",
        min_nights=3,
        max_nights=3,
        budget_amount=300,
        interests=["muzeji"],
        adults=2,
    )
    r = merge_draft(base, draft)
    c = r.constraints
    assert c.destinations == ["FCO"] and c.anywhere is False
    assert c.date_mode.value == "flexible"  # exact mode without dates was rejected
    assert (c.min_nights, c.max_nights) == (3, 3)
    assert c.budget_amount == Decimal("300.0")
    assert c.interests == ["muzeji"]
    assert any(n.severity == "warning" and "Nije primenjeno" in n.message for n in r.notes)


def test_review_floor_cannot_be_lowered_by_text():
    r = merge_draft(make_constraints(), CoordinatorDraft(min_review_score=6.0))
    assert r.constraints.min_review_score == Decimal("7.5")
    assert any("7.5" in n.message for n in r.notes)
