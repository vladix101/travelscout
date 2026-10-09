"""TravelScout FastAPI application."""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agents.coordinator import interpret_request
from app.agents.itinerary_agent import generate_itinerary
from app.agents.llm import UNTRUSTED_DATA_RULE, get_chat_model
from app.config import ENV_FILE, REPO_ROOT, get_settings
from app.db import Database
from app.domain.airports import ORIGIN_AIRPORTS
from app.domain.models import InterpretedRequest, Itinerary, SearchConstraints, SearchResult, TripPackage
from app.jobs import JobManager, build_deps
from app.providers.demo import DEMO_ROUTES
from app.providers.manual import ManualAccommodationInput, ManualFlightInput
from app.security import install_log_redaction, redact
from app.services.export import package_to_json, package_to_markdown
from app.services.mailer import MailError, mail_configured, send_trip_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
install_log_redaction()
log = logging.getLogger("travelscout")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    db = Database(settings.database_path)
    await db.mark_stale_jobs()
    app.state.settings = settings
    app.state.db = db
    app.state.jobs = JobManager(settings, db)
    log.info(
        "TravelScout started (model=%s, llm=%s, wizz_live=%s, booking_scraper=%s)",
        settings.openai_model,
        settings.llm_available,
        settings.wizz_live_enabled,
        settings.booking_scraper_enabled,
    )
    yield
    for task in list(app.state.jobs.tasks.values()):
        task.cancel()
    db.close()


app = FastAPI(title="TravelScout API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": redact(f"Interna greška: {type(exc).__name__}")})


def _db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


# --- meta / integrations ------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
async def meta(request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    today = date.today()
    return {
        "origins": [{"iata": a.iata, "city": a.city, "country": a.country} for a in ORIGIN_AIRPORTS],
        "demo_destinations": list(DEMO_ROUTES),
        "llm_available": s.llm_available,
        "model": s.openai_model,
        "live_flights": s.wizz_live_enabled,
        "live_accommodation": s.booking_scraper_enabled,
        "max_shortlist": s.max_shortlist,
        "defaults": SearchConstraints(
            window_start=today + timedelta(days=21), window_end=today + timedelta(days=35)
        ).model_dump(mode="json"),
    }


def _playwright_ready() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    browsers = Path.home() / "AppData" / "Local" / "ms-playwright"
    alt = Path.home() / ".cache" / "ms-playwright"
    return any(p.exists() and any(p.glob("chromium-*")) for p in (browsers, alt))


@app.get("/api/integrations")
async def integrations(request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    jobs: JobManager = request.app.state.jobs
    items = [
        {
            "id": "openai",
            "name": "OpenAI (agenti)",
            "status": "active" if s.llm_available else "missing",
            "detail": f"Model: {s.openai_model}" if s.llm_available else "Dodajte OPENAI_API_KEY u .env",
            "data": "LLM tumačenje, orkestracija alata, objašnjenja",
        },
        {
            "id": "wizz_live",
            "name": "Wizz Air – javni podaci sa wizzair.com",
            "status": "active" if s.wizz_live_enabled else "disabled",
            "detail": "Mapa ruta + kalendar najnižih cena (timetable). Bez brojeva letova, vremena sletanja i cena "
            "prtljaga – detaljna pretraga je zaštićena (Kasada) i ne zaobilazimo je. Nezvanično; može prestati "
            "da radi.",
            "data": "live / cached",
        },
        {
            "id": "wizz_api",
            "name": "Wizz Air zvanični API",
            "status": "unavailable",
            "detail": "Wizz Air ne nudi javni ni partnerski API za cene.",
            "data": "—",
        },
        {
            "id": "booking_browser",
            "name": "Booking.com – pregledač (Playwright)",
            "status": ("active" if _playwright_ready() else "error") if s.booking_scraper_enabled else "disabled",
            "detail": (
                "Vidljiv Chromium prozor otvara Booking.com za tačne datume i čita tabelu soba (ocena, kupatilo "
                "po sobi, kapacitet, cena sa taksama, otkazivanje). CAPTCHA se ne zaobilazi."
                + ("" if _playwright_ready() else " Chromium nije instaliran: python -m playwright install chromium")
            ),
            "data": "live / cached",
        },
        {
            "id": "booking_api",
            "name": "Booking.com Demand API",
            "status": "unavailable",
            "detail": "Zahteva ugovor Managed Affiliate Partner-a sa Booking.com; nije podržano.",
            "data": "—",
        },
        {
            "id": "osm",
            "name": "OpenStreetMap (Nominatim + Overpass)",
            "status": "active" if s.osm_enabled else "disabled",
            "detail": "Znamenitosti, koordinate, OSM oznake radnog vremena/ulaznica za planove putovanja.",
            "data": "live / cached",
        },
        {
            "id": "fx",
            "name": "Kursevi (ECB + NBS)",
            "status": "active" if s.fx_live_enabled else "disabled",
            "detail": "ECB preko frankfurter.dev; RSD srednji kurs NBS preko kurs.resenje.org. "
            "Rezervni kursevi su označeni.",
            "data": "live / cached / fallback",
        },
        {
            "id": "email",
            "name": "Slanje mejla (Gmail SMTP)",
            "status": "active" if mail_configured(s) else "missing",
            "detail": f"Pošiljalac: {s.gmail_address}"
            if mail_configured(s)
            else "Dodajte GMAIL_ADDRESS i GOOGLE_APP_PASSWORD u .env",
            "data": "—",
        },
        {
            "id": "manual",
            "name": "Ručni uvoz",
            "status": "active",
            "detail": "Letovi i smeštaj koje unesete sami.",
            "data": "manual",
        },
        {
            "id": "demo",
            "name": "DEMO skup podataka",
            "status": "active",
            "detail": "Izmišljeni podaci za isprobavanje celog toka; nikad se ne mešaju sa živim podacima.",
            "data": "demo",
        },
        {
            "id": "links",
            "name": "Linkovi za pretragu kod provajdera",
            "status": "active",
            "detail": "Wizz Air i Booking.com linkovi sa unetim datumima i putnicima.",
            "data": "—",
        },
        {
            "id": "langsmith",
            "name": "LangSmith tracing",
            "status": "active" if (s.langsmith_tracing and s.langsmith_api_key) else "disabled",
            "detail": "Opciono; kada je uključeno šalje promptove, pozive alata i odgovore modela na LangSmith.",
            "data": "—",
        },
    ]
    return {
        "items": items,
        "last_run": jobs.last_provider_status,
        "env_file_found": ENV_FILE.is_file(),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


# --- coordinator / search -----------------------------------------------------------------------


class InterpretBody(BaseModel):
    constraints: SearchConstraints
    text: str | None = Field(default=None, max_length=2000)


@app.post("/api/interpret", response_model=InterpretedRequest)
async def interpret(body: InterpretBody, request: Request) -> InterpretedRequest:
    return await interpret_request(body.constraints, body.text, get_chat_model(request.app.state.settings))


class SearchBody(BaseModel):
    constraints: SearchConstraints
    mode: Literal["demo", "live"] = "demo"
    # True: pause after the flight search so the user picks flights before accommodation is searched.
    select_flights: bool = True


@app.post("/api/searches")
async def start_search(body: SearchBody, request: Request) -> dict[str, str]:
    job_id = await request.app.state.jobs.start(body.constraints, body.mode, body.select_flights)
    return {"job_id": job_id}


class SelectBody(BaseModel):
    flight_ids: list[str] = Field(min_length=1, max_length=15)


@app.post("/api/searches/{job_id}/select")
async def select_flights(job_id: str, body: SelectBody, request: Request) -> dict[str, bool]:
    if not await request.app.state.jobs.select(job_id, body.flight_ids):
        raise HTTPException(409, "Ova pretraga ne čeka izbor letova (završena, otkazana ili je server restartovan).")
    return {"ok": True}


@app.get("/api/searches/{job_id}")
async def get_search(job_id: str, request: Request) -> dict[str, Any]:
    job = await _db(request).get_job(job_id)
    if not job:
        raise HTTPException(404, "Pretraga nije pronađena")
    jobs: JobManager = request.app.state.jobs
    if job_id in jobs.progress and job["status"] in ("queued", "running", "awaiting_selection"):
        job["progress"] = jobs.progress[job_id]
    return job


@app.post("/api/searches/{job_id}/cancel")
async def cancel_search(job_id: str, request: Request) -> dict[str, bool]:
    return {"cancelled": await request.app.state.jobs.cancel(job_id)}


async def _load_package(request: Request, job_id: str, package_id: str) -> tuple[dict[str, Any], TripPackage]:
    job = await _db(request).get_job(job_id)
    if not job or not job.get("result"):
        raise HTTPException(404, "Rezultat pretrage nije pronađen")
    result = SearchResult.model_validate(job["result"])
    pkg = next((p for p in result.packages if p.id == package_id), None)
    if pkg is None:
        raise HTTPException(404, "Paket nije pronađen")
    return job, pkg


@app.post("/api/searches/{job_id}/packages/{package_id}/itinerary", response_model=Itinerary)
async def itinerary(job_id: str, package_id: str, request: Request) -> Itinerary:
    job, pkg = await _load_package(request, job_id, package_id)
    it = await _itinerary_for(request, job, pkg, generate=True)
    assert it is not None
    return it


@app.get("/api/searches/{job_id}/packages/{package_id}/export")
async def export(
    job_id: str, package_id: str, request: Request, format: Literal["md", "json"] = "md"
) -> PlainTextResponse:
    _, pkg = await _load_package(request, job_id, package_id)
    cached = await _db(request).cache_get(f"itinerary:{job_id}:{package_id}")
    it = Itinerary.model_validate(cached[0]) if cached else None
    name = f"travelscout-{pkg.destination.lower()}-{pkg.flight.outbound.departure_local:%Y%m%d}"
    if format == "json":
        return PlainTextResponse(
            package_to_json(pkg, it),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )
    return PlainTextResponse(
        package_to_markdown(pkg, it),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


# --- saved searches ----------------------------------------------------------------------------


class SavedSearchBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    constraints: SearchConstraints


@app.get("/api/saved-searches")
async def list_saved(request: Request) -> list[dict[str, Any]]:
    return await _db(request).list_saved_searches()


@app.post("/api/saved-searches")
async def add_saved(body: SavedSearchBody, request: Request) -> dict[str, str]:
    return {"id": await _db(request).add_saved_search(body.name, body.constraints.model_dump(mode="json"))}


@app.delete("/api/saved-searches/{sid}")
async def delete_saved(sid: str, request: Request) -> dict[str, bool]:
    await _db(request).delete_saved_search(sid)
    return {"ok": True}


# --- my trips (flight + accommodation + itinerary) and e-mail -----------------------------------


class TripSaveBody(BaseModel):
    job_id: str
    package_id: str
    note: str | None = Field(default=None, max_length=500)
    include_itinerary: bool = True


async def _itinerary_for(request: Request, job: dict[str, Any], pkg: TripPackage, generate: bool) -> Itinerary | None:
    db = _db(request)
    key = f"itinerary:{job['id']}:{pkg.id}"
    cached = await db.cache_get(key)
    if cached:
        return Itinerary.model_validate(cached[0])
    if not generate:
        return None
    constraints = SearchConstraints.model_validate(job["request"])
    deps = await build_deps(request.app.state.settings, db, job["mode"])
    it = await generate_itinerary(pkg, constraints.interests, constraints.pace, deps)
    await db.cache_set(key, it.model_dump(mode="json"), 30 * 86400)
    return it


@app.get("/api/trips")
async def list_trips(request: Request) -> list[dict[str, Any]]:
    return await _db(request).list_trips()


@app.post("/api/trips")
async def save_trip(body: TripSaveBody, request: Request) -> dict[str, Any]:
    job, pkg = await _load_package(request, body.job_id, body.package_id)
    it = await _itinerary_for(request, job, pkg, body.include_itinerary)
    title = (
        f"{pkg.destination_name} {pkg.flight.outbound.departure_local:%d.%m.}–"
        f"{pkg.flight.inbound.departure_local:%d.%m.%Y}"
    )
    tid = await _db(request).add_trip(
        body.job_id, title, body.note, pkg.model_dump(mode="json"), it.model_dump(mode="json") if it else None
    )
    trip = await _db(request).get_trip(tid)
    assert trip is not None
    return trip


async def _load_trip(request: Request, tid: str) -> tuple[dict[str, Any], TripPackage, Itinerary | None]:
    trip = await _db(request).get_trip(tid)
    if not trip:
        raise HTTPException(404, "Putovanje nije pronađeno")
    pkg = TripPackage.model_validate(trip["package"])
    it = Itinerary.model_validate(trip["itinerary"]) if trip["itinerary"] else None
    return trip, pkg, it


@app.get("/api/trips/{tid}")
async def get_trip(tid: str, request: Request) -> dict[str, Any]:
    trip, _, _ = await _load_trip(request, tid)
    return trip


@app.delete("/api/trips/{tid}")
async def delete_trip(tid: str, request: Request) -> dict[str, bool]:
    await _db(request).delete_trip(tid)
    return {"ok": True}


@app.get("/api/trips/{tid}/export")
async def export_trip(tid: str, request: Request, format: Literal["md", "json"] = "md") -> PlainTextResponse:
    _, pkg, it = await _load_trip(request, tid)
    name = f"travelscout-{pkg.destination.lower()}-{pkg.flight.outbound.departure_local:%Y%m%d}"
    if format == "json":
        return PlainTextResponse(
            package_to_json(pkg, it),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )
    return PlainTextResponse(
        package_to_markdown(pkg, it),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


class EmailBody(BaseModel):
    to: str | None = Field(default=None, max_length=254)


@app.post("/api/searches/{job_id}/packages/{package_id}/email")
async def email_package(job_id: str, package_id: str, body: EmailBody, request: Request) -> dict[str, Any]:
    """Send a trip straight from search results without saving it."""
    s = request.app.state.settings
    job, pkg = await _load_package(request, job_id, package_id)
    it = await _itinerary_for(request, job, pkg, generate=True)
    to = (body.to or s.mail_to or s.gmail_address or "").strip()
    try:
        await send_trip_email(s, to, pkg, it)
    except MailError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "to": to}


@app.get("/api/mail/status")
async def mail_status(request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    return {
        "configured": mail_configured(s),
        "sender": s.gmail_address or None,
        "default_to": s.mail_to or s.gmail_address or None,
    }


@app.post("/api/trips/{tid}/email")
async def email_trip(tid: str, body: EmailBody, request: Request) -> dict[str, Any]:
    s = request.app.state.settings
    trip, pkg, it = await _load_trip(request, tid)
    to = (body.to or s.mail_to or s.gmail_address or "").strip()
    try:
        await send_trip_email(s, to, pkg, it, trip.get("note"))
    except MailError as exc:
        raise HTTPException(400, str(exc)) from exc
    await _db(request).update_trip(tid, emailed=True)
    return {"ok": True, "to": to}


# --- manual import ------------------------------------------------------------------------------


@app.get("/api/manual")
async def list_manual(request: Request) -> list[dict[str, Any]]:
    return await _db(request).list_manual_offers()


@app.post("/api/manual/flight")
async def add_manual_flight(body: ManualFlightInput, request: Request) -> dict[str, str]:
    return {"id": await _db(request).add_manual_offer("flight", body.model_dump(mode="json"))}


@app.post("/api/manual/accommodation")
async def add_manual_accommodation(body: ManualAccommodationInput, request: Request) -> dict[str, str]:
    return {"id": await _db(request).add_manual_offer("accommodation", body.model_dump(mode="json"))}


@app.delete("/api/manual/{mid}")
async def delete_manual(mid: str, request: Request) -> dict[str, bool]:
    await _db(request).delete_manual_offer(mid)
    return {"ok": True}


class ExtractBody(BaseModel):
    kind: Literal["flight", "accommodation"]
    text: str = Field(min_length=10, max_length=6000)


@app.post("/api/manual/extract")
async def extract_manual(body: ExtractBody, request: Request) -> dict[str, Any]:
    """LLM-assisted extraction of pasted offer text into a draft the user reviews before saving."""
    llm = get_chat_model(request.app.state.settings)
    if llm is None:
        raise HTTPException(400, "Izdvajanje iz teksta zahteva OPENAI_API_KEY.")
    schema = ManualFlightInput if body.kind == "flight" else ManualAccommodationInput
    prompt = (
        "Izdvoji podatke o ponudi iz teksta koji je korisnik nalepio. Popuni samo ono što eksplicitno piše; "
        "nepoznato ostavi null/unknown. Kupatilo označi kao private/ensuite/private_external/shared samo ako je "
        "izričito navedeno za sobu. " + UNTRUSTED_DATA_RULE
    )
    try:
        draft = await llm.with_structured_output(schema).ainvoke(
            [SystemMessage(prompt), HumanMessage(f"<<<\n{body.text}\n>>>")]
        )
    except ValidationError as exc:
        raise HTTPException(422, f"Izdvojeni podaci nisu validni: {exc.error_count()} grešaka") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, redact(f"Izdvajanje nije uspelo: {type(exc).__name__}")) from exc
    return {"draft": draft.model_dump(mode="json"), "note": "Proverite sva polja pre čuvanja."}  # type: ignore[union-attr]


# --- production frontend ------------------------------------------------------------------------

_dist = REPO_ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
