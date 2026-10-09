"""Accommodation Agent (Booking.com).

Searching and eligibility are deterministic: the agent calls the accommodation providers for
the *exact* stay dates derived from the flights, and Python rules decide eligibility.
The LLM is used only to write short explanations from typed, already-validated facts
(never from raw page text), so prompt injection via listings has no path to the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.context import RunDeps
from app.agents.llm import UNTRUSTED_DATA_RULE
from app.domain.models import AccommodationOffer, FlightOption, SearchConstraints, TripPackage
from app.providers.base import DestinationInfo, ProviderBlockedError, ProviderError
from app.services.timeplan import stay_dates

log = logging.getLogger(__name__)

BROWSER_PROVIDER_TIMEOUT = 300.0


@dataclass
class TripAccommodation:
    trip_id: str
    destination: str
    checkin: date
    checkout: date
    offers: list[AccommodationOffer] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def search_for_trip(
    trip: FlightOption, dest: DestinationInfo, constraints: SearchConstraints, deps: RunDeps
) -> TripAccommodation:
    checkin, checkout, _ = stay_dates(trip.outbound, trip.inbound)
    place = dest.city or dest.name
    result = TripAccommodation(trip_id=trip.id, destination=dest.iata, checkin=checkin, checkout=checkout)
    await deps.emit(
        "accommodation",
        f"Smeštaj: {place} {checkin:%d.%m.}–{checkout:%d.%m.%Y}",
        agent="Accommodation Agent",
        tool="search_accommodation",
    )
    for p in deps.accommodation_providers:
        timeout = BROWSER_PROVIDER_TIMEOUT if "pregledač" in p.name else None
        try:
            offers = await deps.with_timeout(p.search(constraints, dest, checkin, checkout), timeout)
            result.offers += offers
            deps.record(
                p.name, "ok", f"{place}: {len(offers)} soba/ponuda", offers[0].evidence.origin if offers else None
            )
        except ProviderBlockedError as exc:
            result.errors.append(f"{p.name}: {exc}")
            deps.record(p.name, "failed", f"{place}: {exc}")
            await deps.emit("accommodation", f"{p.name}: blokirano ({place})", "warning", agent="Accommodation Agent")
        except (ProviderError, TimeoutError) as exc:
            msg = str(exc) or "isteklo vreme"
            result.errors.append(f"{p.name}: {msg}")
            deps.record(p.name, "failed", f"{place}: {msg}")
            await deps.emit("accommodation", f"{p.name}: greška ({place})", "warning", agent="Accommodation Agent")
        except Exception as exc:  # noqa: BLE001
            log.exception("accommodation provider crashed")
            result.errors.append(f"{p.name}: {type(exc).__name__}")
            deps.record(p.name, "failed", f"{place}: interna greška")
    return result


class PackageNote(BaseModel):
    package_id: str
    note: str = Field(description="1-2 rečenice na srpskom: zašto je smeštaj dobar izbor i šta proveriti")


class AccommodationNotes(BaseModel):
    notes: list[PackageNote]


ACCOMMODATION_PROMPT = """Ti si Accommodation Agent aplikacije TravelScout.
Dobijaš već proverene činjenice o izabranom smeštaju za svaki paket (JSON). Za svaki paket napiši 1-2 rečenice
na srpskom (latinica): šta je dobro (ocena, lokacija, kupatilo, otkazivanje) i šta korisnik treba da proveri
pre rezervacije. Koristi isključivo date činjenice; ne dodaj nove cene ni tvrdnje. {rule}"""


async def explain_packages(packages: list[TripPackage], deps: RunDeps) -> dict[str, str]:
    if deps.llm is None or not packages:
        return {}
    facts = []
    for p in packages[:6]:
        o = p.accommodation.offer
        facts.append(
            {
                "package_id": p.id,
                "destination": p.destination_name,
                "property_type": o.property_type,
                "review_score": str(o.review_score) if o.review_score is not None else None,
                "review_count": o.review_count,
                "room": o.room_name,
                "bathroom": o.bathroom.value,
                "distance_km": str(o.distance_km) if o.distance_km is not None else None,
                "cancellation": o.cancellation,
                "prepayment": o.prepayment,
                "unknown_costs": p.cost.unknown_items,
                "data_origin": o.evidence.origin.value,
            }
        )
    try:
        structured = deps.llm.with_structured_output(AccommodationNotes)
        out = await asyncio.wait_for(
            structured.ainvoke(
                [
                    SystemMessage(ACCOMMODATION_PROMPT.format(rule=UNTRUSTED_DATA_RULE)),
                    HumanMessage(json.dumps(facts, ensure_ascii=False)),
                ]
            ),
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("accommodation notes failed: %s", type(exc).__name__)
        return {}
    assert isinstance(out, AccommodationNotes)
    valid = {p.id for p in packages}
    return {n.package_id: n.note[:400] for n in out.notes if n.package_id in valid}
