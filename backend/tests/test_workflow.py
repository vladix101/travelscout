from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.agents.context import RunDeps
from app.agents.itinerary_agent import generate_itinerary
from app.config import get_settings
from app.db import Database
from app.domain.models import DataOrigin, EligibilityStatus, Pace, PointOfInterest, RankingPreferences
from app.graph.workflow import SEARCH_GRAPH
from app.jobs import assemble_result, build_deps
from app.providers.base import ProviderError
from app.providers.demo import DemoAccommodationProvider, DemoFlightProvider
from app.providers.manual import (
    ManualAccommodationInput,
    ManualAccommodationProvider,
    ManualFlightInput,
    ManualFlightProvider,
)
from app.providers.osm import osm_map_url
from app.services.money import FxTable
from tests.conftest import make_constraints


def future_constraints(**kw):
    start = date.today() + timedelta(days=30)
    return make_constraints(window_start=start, window_end=start + timedelta(days=10), min_nights=3, max_nights=4, **kw)


def deps_with(flights, stays, poi=None, db=None):
    return RunDeps(
        settings=get_settings(),
        mode="live",
        fx=FxTable.fallback(),
        fx_status="test",
        flight_providers=flights,
        accommodation_providers=stays,
        poi_provider=poi,
        llm=None,
        db=db,
    )


async def run(constraints, deps):
    state = await SEARCH_GRAPH.ainvoke({"constraints": constraints}, config={"configurable": {"deps": deps}})
    return assemble_result(state, constraints, deps, partial=False)


async def test_demo_workflow_returns_only_eligible_ranked_packages():
    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()])
    deps.mode = "demo"
    res = await run(
        future_constraints(ranking=RankingPreferences(quality_weight=0, location_weight=0, time_weight=0)), deps
    )
    assert res.packages, res.suggestions
    for p in res.packages:
        assert p.accommodation.eligibility.status is EligibilityStatus.ELIGIBLE
        offer = p.accommodation.offer
        assert offer.review_score is not None and offer.review_score >= Decimal("7.5")
        assert "Hostel" not in (offer.property_type or "")
        assert offer.bathroom.value in ("ensuite", "private", "private_external", "entire_unit")
        assert p.data_origins == [DataOrigin.DEMO]
    totals = [p.cost.comparable_total for p in res.packages]
    assert totals == sorted(totals)
    assert {"hostel", "review_score_below_min", "shared_bathroom"} <= set(res.rejected_counts)
    assert res.needs_verification
    assert all(n.eligibility.status is EligibilityStatus.NEEDS_VERIFICATION for n in res.needs_verification)


async def test_bigger_party_excludes_small_rooms():
    from app.domain.models import Party

    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()])
    res = await run(future_constraints(party=Party(adults=4)), deps)
    for p in res.packages:
        assert p.accommodation.offer.max_occupancy >= 4
    assert res.rejected_counts.get("occupancy_insufficient", 0) > 0


class FailingFlights:
    name = "Failing flights"

    async def list_destinations(self, origin, direct_only):
        raise ProviderError("simulated outage")

    async def search_round_trips(self, constraints, origin, destination):
        raise ProviderError("simulated outage")


class FailingStays:
    name = "Failing stays"

    async def search(self, constraints, destination, checkin, checkout):
        raise ProviderError("simulated outage")


async def _manual_db(tmp_path, c):
    db = Database(tmp_path / "w.db")
    d0 = c.window_start
    await db.add_manual_offer(
        "flight",
        ManualFlightInput.model_validate(
            {
                "origin": "BEG",
                "destination": "FCO",
                "outbound": {"departure_local": f"{d0}T15:50:00", "arrival_local": f"{d0}T17:25:00"},
                "inbound": {
                    "departure_local": f"{d0 + timedelta(days=4)}T18:10:00",
                    "arrival_local": f"{d0 + timedelta(days=4)}T19:45:00",
                },
                "price_per_seat_outbound": "60",
                "price_per_seat_inbound": "50",
            }
        ).model_dump(mode="json"),
    )
    await db.add_manual_offer(
        "accommodation",
        ManualAccommodationInput.model_validate(
            {
                "destination": "FCO",
                "property_name": "Imported Hotel",
                "property_type": "Hotel",
                "review_score": "8.2",
                "room_name": "Double Room",
                "room_privacy": "private_room",
                "bathroom": "ensuite",
                "max_occupancy": 2,
                "checkin": str(d0),
                "checkout": str(d0 + timedelta(days=4)),
                "availability_confirmed": True,
                "total_price": "320",
            }
        ).model_dump(mode="json"),
    )
    return db


async def test_provider_failure_keeps_partial_results_without_fabrication(tmp_path):
    c = future_constraints()
    db = await _manual_db(tmp_path, c)
    fx = FxTable.fallback()
    deps = deps_with(
        [FailingFlights(), ManualFlightProvider(db, fx)], [FailingStays(), ManualAccommodationProvider(db)], db=db
    )
    res = await run(c, deps)
    assert len(res.packages) == 1
    pkg = res.packages[0]
    assert pkg.data_origins == [DataOrigin.MANUAL]
    assert pkg.accommodation.offer.property_name == "Imported Hotel"
    statuses = {s.provider: s.status for s in res.provider_status}
    assert statuses["Failing flights"] == "failed"
    db.close()


async def test_all_providers_failing_yields_no_packages_and_suggestions():
    deps = deps_with([FailingFlights()], [FailingStays()])
    res = await run(future_constraints(), deps)
    assert res.packages == []
    assert res.suggestions
    assert any(d.code == "provider_errors" for d in res.diagnostics)


async def test_demo_and_live_providers_are_never_mixed(tmp_path, monkeypatch):
    db = Database(tmp_path / "s.db")
    settings = get_settings()
    demo = await build_deps(settings, db, "demo")
    assert {type(p).__name__ for p in demo.flight_providers} == {"DemoFlightProvider"}
    assert {type(p).__name__ for p in demo.accommodation_providers} == {"DemoAccommodationProvider"}
    live = await build_deps(settings, db, "live")
    names = {type(p).__name__ for p in live.flight_providers + live.accommodation_providers}
    assert not any(n.startswith("Demo") for n in names)
    assert {s.status for s in live.provider_status} == {"disabled"}  # Wizz/Booking disabled in tests
    db.close()


async def test_past_dates_stop_the_workflow():
    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()])
    past = make_constraints(window_start=date(2020, 1, 1), window_end=date(2020, 1, 5))
    res = await run(past, deps)
    assert res.packages == [] and any(d.code == "dates_in_past" for d in res.diagnostics)


class FakePoi:
    name = "fake osm"

    async def geocode(self, query):
        return (41.9, 12.48)

    async def attractions(self, lat, lon, radius_m, limit):
        pts = [
            ("Colosseum", "attraction", "yes"),
            ("Pantheon", "attraction", "no"),
            ("Villa Borghese", "park", None),
            ("Trevi Fountain", "attraction", "no"),
            ("Capitoline Museums", "museum", "yes"),
        ]
        return [
            PointOfInterest(
                id=f"osm-n{i}",
                name=n,
                category=cat,
                latitude=41.89 + i / 100,
                longitude=12.48,
                fee=fee,
                map_url=osm_map_url(41.89 + i / 100, 12.48),
                source="OpenStreetMap",
                origin=DataOrigin.LIVE,
            )
            for i, (n, cat, fee) in enumerate(pts)
        ]


async def test_rules_itinerary_respects_trip_window_and_marks_verification():
    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()], poi=FakePoi())
    res = await run(future_constraints(), deps)
    pkg = res.packages[0]
    it = await generate_itinerary(pkg, ["istorija"], Pace.MODERATE, deps)
    first = pkg.flight.outbound.departure_local.date()
    last = pkg.flight.inbound.departure_local.date()
    assert it.days and all(first <= d.date <= last for d in it.days)
    sights = [i for d in it.days for i in d.items if i.kind == "sight"]
    assert sights and all(i.verified and i.map_url and i.poi_id for i in sights)
    assert any("procene" in d for d in it.disclaimers)
    assert all(t.verified is False for t in it.transfers)


@pytest.mark.live
async def test_live_llm_flight_agent_demo_mode():
    """Optional: exercises the real OpenAI agents on demo data. Run with: pytest -m live"""
    import os

    from app.agents.llm import get_chat_model
    from app.config import Settings, load_env_file

    load_env_file()
    settings = Settings(openai_api_key=os.environ.get("OPENAI_API_KEY") or None)
    llm = get_chat_model(settings)
    if llm is None:
        pytest.skip("OPENAI_API_KEY not configured")
    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()])
    deps.llm = llm
    res = await run(future_constraints(), deps)
    assert res.used_llm and res.packages


def test_match_poi_links_free_text_to_real_pois():
    from app.agents.itinerary_agent import match_poi

    pois = {
        p.id: p
        for p in [
            PointOfInterest(
                id="a",
                name="Pantheon",
                category="attraction",
                latitude=1,
                longitude=1,
                map_url="https://www.openstreetmap.org/x",
                source="OpenStreetMap",
                origin=DataOrigin.LIVE,
            ),
            PointOfInterest(
                id="b",
                name="Fontana di Trevi",
                category="attraction",
                latitude=1,
                longitude=1,
                map_url="https://www.openstreetmap.org/y",
                source="OpenStreetMap",
                origin=DataOrigin.LIVE,
            ),
        ]
    }
    assert match_poi("Poseta: Pantheon (unutra)", pois).id == "a"
    assert match_poi("Večernja šetnja do Fontána di Trevi", pois).id == "b"
    assert match_poi("Ručak u centru", pois) is None


def test_overpass_parse_prefers_notable_and_dedupes():
    from app.providers.osm import parse_overpass

    data = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 41.9,
                "lon": 12.5,
                "tags": {"name": "Small Statue", "historic": "memorial"},
            },
            {
                "type": "way",
                "id": 2,
                "center": {"lat": 41.89, "lon": 12.49},
                "tags": {
                    "name": "Colosseo",
                    "name:en": "Colosseum",
                    "tourism": "attraction",
                    "wikipedia": "en:Colosseum",
                    "fee": "yes",
                    "website": "http://insecure.example",
                },
            },
            {
                "type": "node",
                "id": 3,
                "lat": 41.9,
                "lon": 12.5,
                "tags": {"name": "Small Statue", "historic": "memorial"},
            },
        ]
    }
    pois = parse_overpass(data, 10)
    assert [p.name for p in pois] == ["Colosseum", "Small Statue"]
    assert pois[0].fee == "yes" and pois[0].website is None  # non-https links are dropped
    assert pois[0].wikipedia == "https://en.wikipedia.org/wiki/Colosseum"


def test_missing_llm_days_are_filled_from_rules():
    from app.agents.itinerary_agent import (
        DraftDay,
        DraftItem,
        ItineraryDraft,
        fill_missing_days,
        transfer_minutes,
        trip_window,
    )
    from tests.test_ranking import pkg as ranking_pkg

    p = ranking_pkg("x", "400")
    free_from, free_until = trip_window(p, transfer_minutes(p))
    expected = {
        (free_from.date() + timedelta(days=i)).isoformat()
        for i in range((free_until.date() - free_from.date()).days + 1)
    }
    only_first = ItineraryDraft(
        days=[
            DraftDay(
                date=free_from.date().isoformat(),
                title="Dolazak",
                items=[DraftItem(start="18:00", end="19:00", title="Šetnja", kind="free_time")],
            )
        ]
    )
    filled = fill_missing_days(only_first, {"package": p, "pace": Pace.MODERATE, "pois": [], "centre": (41.9, 12.48)})
    assert {d.date for d in filled.days} == expected
    assert filled.days[0].title == "Dolazak"  # the model's own day is kept
