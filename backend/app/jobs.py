"""Background search jobs: dependency wiring, progress persistence, cancellation, time budget."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from app.agents.context import ProgressEvent, RunDeps
from app.agents.llm import get_chat_model
from app.config import Settings
from app.db import Database
from app.domain.models import ProviderRunStatus, SearchConstraints, SearchResult
from app.graph.workflow import SEARCH_GRAPH, SearchState, build_suggestions
from app.providers.base import AccommodationProvider, FlightProvider
from app.providers.booking import BookingBrowserProvider
from app.providers.demo import DemoAccommodationProvider, DemoFlightProvider
from app.providers.fx import load_fx_table
from app.providers.manual import ManualAccommodationProvider, ManualFlightProvider
from app.providers.osm import OsmPoiProvider
from app.providers.wizz import WizzLiveProvider
from app.security import redact

log = logging.getLogger(__name__)

Mode = Literal["demo", "live"]


async def build_deps(settings: Settings, db: Database, mode: Mode, on_progress: Any = None) -> RunDeps:
    fx, fx_status = await load_fx_table(db, live=settings.fx_live_enabled)
    flights: list[FlightProvider] = []
    stays: list[AccommodationProvider] = []
    deps = RunDeps(
        settings=settings,
        mode=mode,
        fx=fx,
        fx_status=fx_status,
        flight_providers=flights,
        accommodation_providers=stays,
        poi_provider=OsmPoiProvider(db, settings.contact_email) if settings.osm_enabled else None,
        llm=get_chat_model(settings),
        db=db,
        on_progress=on_progress,
    )
    if mode == "demo":
        flights.append(DemoFlightProvider(fx))
        stays.append(DemoAccommodationProvider())
        return deps
    if settings.wizz_live_enabled:
        flights.append(
            WizzLiveProvider(
                db,
                fx,
                timeout=settings.provider_timeout_seconds,
                cache_ttl=settings.cache_ttl_minutes * 60,
                concurrency=settings.provider_concurrency,
            )
        )
    else:
        deps.record("Wizz Air (wizzair.com – javni podaci)", "disabled", "WIZZ_LIVE_ENABLED=false")
    flights.append(ManualFlightProvider(db, fx))
    if settings.booking_scraper_enabled:
        stays.append(
            BookingBrowserProvider(
                db,
                headless=settings.booking_headless,
                max_properties=settings.max_properties_per_destination,
                cache_ttl=settings.cache_ttl_minutes * 60,
                concurrency=settings.booking_concurrency,
            )
        )
    else:
        deps.record("Booking.com (pregledač)", "disabled", "BOOKING_SCRAPER_ENABLED=false")
    stays.append(ManualAccommodationProvider(db))
    return deps


def assemble_result(
    state: SearchState, constraints: SearchConstraints, deps: RunDeps, *, partial: bool
) -> SearchResult:
    return SearchResult(
        constraints=constraints,
        mode=deps.mode,
        packages=state.get("packages", []),
        flight_shortlist=state.get("shortlist", []),
        destination_cities={k: (d.city or d.name) for k, d in state.get("destinations", {}).items()},
        needs_verification=state.get("needs_verification", []),
        rejected_counts=state.get("rejected_counts", {}),
        diagnostics=state.get("diagnostics", []),
        suggestions=state.get("suggestions") or build_suggestions(state, state.get("packages", [])),
        provider_status=aggregate_status(deps.provider_status),
        agent_notes=state.get("agent_notes", []),
        used_llm=bool(state.get("used_llm")),
        fx_status=deps.fx_status,
        partial=partial,
        generated_at=datetime.now(UTC),
    )


STATUS_ORDER = {"failed": 4, "partial": 3, "ok": 2, "skipped": 1, "disabled": 0}


def aggregate_status(items: list[ProviderRunStatus]) -> list[ProviderRunStatus]:
    """One row per provider: worst status wins; messages are summarised."""
    grouped: dict[str, list[ProviderRunStatus]] = {}
    for it in items:
        grouped.setdefault(it.provider, []).append(it)
    out = []
    for name, rows in grouped.items():
        statuses = {r.status for r in rows}
        if "ok" in statuses and ("failed" in statuses or "partial" in statuses):
            status = "partial"
        else:
            status = max(statuses, key=lambda s: STATUS_ORDER[s])
        msgs = [r.message for r in rows if r.message]
        failed_msgs = [r.message for r in rows if r.status in ("failed", "partial") and r.message]
        message = failed_msgs[0] if failed_msgs else (msgs[0] if msgs else None)
        if len(rows) > 1:
            message = f"{len(rows)} poziva; " + (message or "")
        origin = next((r.origin for r in rows if r.origin), None)
        out.append(
            ProviderRunStatus(
                provider=name,
                status=status,
                message=message,
                origin=origin,  # type: ignore[arg-type]
                checked_at=max(r.checked_at for r in rows if r.checked_at),
            )
        )
    return out


class JobManager:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.progress: dict[str, list[dict[str, Any]]] = {}
        self.last_provider_status: list[dict[str, Any]] = []

    async def start(self, constraints: SearchConstraints, mode: Mode) -> str:
        job_id = await self.db.create_job(mode, constraints.model_dump(mode="json"))
        self.progress[job_id] = []
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, constraints, mode))
        return job_id

    async def cancel(self, job_id: str) -> bool:
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def _run(self, job_id: str, constraints: SearchConstraints, mode: Mode) -> None:
        progress = self.progress[job_id]

        async def on_progress(ev: ProgressEvent) -> None:
            progress.append(ev.as_dict())
            await self.db.update_job(job_id, progress=progress)

        await self.db.update_job(job_id, status="running")
        deps = await build_deps(self.settings, self.db, mode, on_progress)
        if mode == "demo":
            await deps.emit("start", "DEMO režim: koriste se izmišljeni podaci, ne stvarne ponude", "warning")
        if deps.llm is None:
            await deps.emit("start", "OpenAI ključ nije podešen – agenti rade po pravilima", "warning")
        last_state: SearchState = {"constraints": constraints}
        status, error = "done", None
        try:
            async with asyncio.timeout(self.settings.max_job_seconds):
                async for chunk in SEARCH_GRAPH.astream(
                    {"constraints": constraints},
                    config={"configurable": {"deps": deps}, "recursion_limit": 40},
                    stream_mode="values",
                ):
                    last_state = chunk  # type: ignore[assignment]
        except TimeoutError:
            status, error = "partial", "Isteklo je maksimalno vreme izvršavanja; prikazani su delimični rezultati."
            await deps.emit("end", error, "warning")
        except asyncio.CancelledError:
            status, error = "cancelled", "Pretraga je otkazana."
            progress.append(ProgressEvent(stage="end", message=error, status="warning").as_dict())
        except Exception as exc:  # noqa: BLE001
            log.exception("search job failed")
            status, error = "failed", redact(f"Greška u radu: {type(exc).__name__}: {exc}")[:300]
            progress.append(ProgressEvent(stage="end", message=error, status="error").as_dict())
        result = assemble_result(last_state, constraints, deps, partial=status != "done")
        if status == "done" and any(s.status in ("failed", "partial") for s in result.provider_status):
            status = "partial"
        if status in ("done", "partial"):
            progress.append(ProgressEvent(stage="end", message="Pretraga završena", status="done").as_dict())
        self.last_provider_status = [s.model_dump(mode="json") for s in result.provider_status]
        await self.db.update_job(
            job_id, status=status, progress=progress, result=result.model_dump(mode="json"), error=error
        )
        self.tasks.pop(job_id, None)
