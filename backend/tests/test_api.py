import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.sqlite3"))
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def constraints():
    start = date.today() + timedelta(days=30)
    return {
        "origins": ["BEG"],
        "date_mode": "flexible",
        "window_start": str(start),
        "window_end": str(start + timedelta(days=10)),
        "min_nights": 3,
        "max_nights": 4,
        "party": {"adults": 2, "children": 0, "child_ages": [], "infants": 0},
    }


def wait_for(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/searches/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_meta_and_integrations(client):
    meta = client.get("/api/meta").json()
    assert meta["llm_available"] is False
    assert any(o["iata"] == "BEG" for o in meta["origins"])
    items = {i["id"]: i for i in client.get("/api/integrations").json()["items"]}
    assert items["booking_api"]["status"] == "unavailable"
    assert items["wizz_api"]["status"] == "unavailable"
    assert items["demo"]["status"] == "active"
    body = str(client.get("/api/integrations").json())
    assert "sk-" not in body


def test_invalid_constraints_are_rejected(client):
    bad = constraints() | {"min_nights": 6, "max_nights": 2}
    assert client.post("/api/searches", json={"constraints": bad, "mode": "demo"}).status_code == 422
    bad2 = constraints() | {"party": {"adults": 1, "children": 1, "child_ages": [], "infants": 0}}
    assert client.post("/api/searches", json={"constraints": bad2, "mode": "demo"}).status_code == 422


def test_demo_search_end_to_end(client):
    r = client.post("/api/searches", json={"constraints": constraints(), "mode": "demo"})
    job_id = r.json()["job_id"]
    job = wait_for(client, job_id)
    # Phase 1: flights only; the workflow pauses with every flight/date combination.
    assert job["status"] == "awaiting_selection", job
    choices = job["result"]["flight_choices"]
    assert choices and job["result"]["awaiting_selection"] and job["result"]["packages"] == []
    combos = {
        (c["outbound"]["destination"], c["outbound"]["departure_local"], c["inbound"]["departure_local"])
        for c in choices
    }
    assert len(combos) == len(choices)  # every option is a distinct destination/date combination
    assert client.post(f"/api/searches/{job_id}/select", json={"flight_ids": [choices[0]["id"]]}).json()
    # Phase 2: accommodation only for the chosen flight.
    job = wait_for(client, job_id)
    assert job["status"] == "done", job
    assert {p["flight"]["id"] for p in job["result"]["packages"]} <= {choices[0]["id"]}
    result = job["result"]
    assert result["mode"] == "demo" and result["packages"]
    assert all(p["data_origins"] == ["demo"] for p in result["packages"])
    stages = {e["stage"] for e in job["progress"]}
    assert {"validate", "flights", "shortlist", "accommodation", "eligibility", "totals", "rank"} <= stages

    pkg = result["packages"][0]
    it = client.post(f"/api/searches/{job['id']}/packages/{pkg['id']}/itinerary").json()
    assert it["days"] and it["used_llm"] is False
    md = client.get(f"/api/searches/{job['id']}/packages/{pkg['id']}/export?format=md")
    assert md.status_code == 200 and "DEMO" in md.text and "## Troškovi" in md.text
    js = client.get(f"/api/searches/{job['id']}/packages/{pkg['id']}/export?format=json").json()
    assert js["package"]["id"] == pkg["id"] and js["itinerary"] is not None

    fav = client.post("/api/favorites", json={"job_id": job["id"], "package": pkg}).json()
    assert client.get("/api/favorites").json()[0]["id"] == fav["id"]
    client.delete(f"/api/favorites/{fav['id']}")
    assert client.get("/api/favorites").json() == []


def test_select_is_rejected_when_not_waiting(client):
    assert client.post("/api/searches/unknown/select", json={"flight_ids": ["x"]}).status_code == 409


def test_automatic_mode_skips_selection(client):
    r = client.post("/api/searches", json={"constraints": constraints(), "mode": "demo", "select_flights": False})
    job = wait_for(client, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["packages"]


def test_live_search_without_providers_reports_instead_of_inventing(client):
    r = client.post("/api/searches", json={"constraints": constraints(), "mode": "live"})
    job = wait_for(client, r.json()["job_id"])
    result = job["result"]
    assert result["packages"] == []
    assert result["suggestions"]
    assert {s["status"] for s in result["provider_status"]} >= {"disabled"}


def test_saved_searches(client):
    sid = client.post("/api/saved-searches", json={"name": "Jesen", "constraints": constraints()}).json()["id"]
    assert client.get("/api/saved-searches").json()[0]["name"] == "Jesen"
    client.delete(f"/api/saved-searches/{sid}")
    assert client.get("/api/saved-searches").json() == []


def test_manual_import_validation(client):
    ok = {
        "destination": "FCO",
        "property_name": "Hotel X",
        "checkin": "2026-11-04",
        "checkout": "2026-11-08",
        "review_score": "8",
        "bathroom": "ensuite",
        "room_privacy": "private_room",
    }
    assert client.post("/api/manual/accommodation", json=ok).status_code == 200
    assert client.post("/api/manual/accommodation", json=ok | {"review_score": "11"}).status_code == 422
    assert client.post("/api/manual/accommodation", json=ok | {"bathroom": "maybe"}).status_code == 422
    assert (
        client.post("/api/manual/extract", json={"kind": "flight", "text": "some pasted text here"}).status_code == 400
    )


def test_interpret_without_llm_keeps_form_values(client):
    r = client.post("/api/interpret", json={"constraints": constraints(), "text": "Rim za dvoje u novembru"}).json()
    assert r["used_llm"] is False
    assert any("OPENAI_API_KEY" in n["message"] for n in r["notes"])


def test_cancel_unknown_job(client):
    assert client.post("/api/searches/nope/cancel").json() == {"cancelled": False}
