"""Itinerary Agent and the on-demand itinerary graph.

gather_facts (geocode + OpenStreetMap attractions) → plan (LLM agent with tools, or rules)
→ finalize (validate POI references, attach map/source links, budgets, disclaimers).

Facts (names, coordinates, OSM opening_hours/fee tags) come only from OpenStreetMap.
Anything the model adds from general knowledge (durations, transfer options) is labelled
unverified. Attractions the model mentions without a valid POI id lose their "verified" flag
and get a map *search* link instead of invented coordinates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents.context import RunDeps
from app.agents.llm import UNTRUSTED_DATA_RULE
from app.domain.airports import KNOWN_AIRPORTS
from app.domain.models import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    Pace,
    PointOfInterest,
    TransferOption,
    TripPackage,
)
from app.providers.base import ProviderError
from app.providers.osm import google_maps_search_url
from app.services.costing import ESTIMATES, cost_tier
from app.services.money import q
from app.services.timeplan import ARRIVAL_BUFFER, DEPARTURE_BUFFER

log = logging.getLogger(__name__)

SIGHTS_PER_DAY = {Pace.RELAXED: 2, Pace.MODERATE: 3, Pace.INTENSE: 5}
DURATION_BY_CATEGORY = {
    "museum": 120,
    "gallery": 90,
    "castle": 90,
    "archaeological_site": 90,
    "attraction": 60,
    "viewpoint": 40,
    "park": 60,
    "garden": 60,
    "monument": 30,
    "memorial": 30,
    "church": 40,
    "place_of_worship": 40,
    "ruins": 45,
}


# --- LLM output schema -----------------------------------------------------------------------


class DraftItem(BaseModel):
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")
    title: str
    kind: Literal["sight", "meal", "transfer", "break", "free_time", "checkin", "checkout"]
    poi_id: str | None = Field(default=None, description="ID iz alata za znamenitosti; null ako nema")
    area: str | None = None
    description: str | None = Field(default=None, description="1 rečenica, srpski")
    paid: bool | None = Field(default=None, description="true ako je verovatno plaćeno; null ako nije poznato")


class DraftDay(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    title: str
    area: str | None = None
    items: list[DraftItem]


class DraftTransfer(BaseModel):
    mode: str
    description: str
    est_duration_min: int | None = None
    est_cost_eur: float | None = None


class ItineraryDraft(BaseModel):
    days: list[DraftDay]
    transfers: list[DraftTransfer] = Field(default_factory=list)


# --- graph state ------------------------------------------------------------------------------


class ItineraryState(TypedDict, total=False):
    package: TripPackage
    interests: list[str]
    pace: Pace
    centre: tuple[float, float] | None
    pois: list[PointOfInterest]
    draft: ItineraryDraft | None
    used_llm: bool
    itinerary: Itinerary
    notes: list[str]


def _deps(config: RunnableConfig) -> RunDeps:
    return config["configurable"]["deps"]  # type: ignore[index, no-any-return]


def trip_window(pkg: TripPackage, transfer_min: int) -> tuple[datetime, datetime]:
    out, inb = pkg.flight.outbound, pkg.flight.inbound
    arrival = out.arrival_local or out.departure_local
    free_from = arrival + ARRIVAL_BUFFER + timedelta(minutes=transfer_min)
    free_until = inb.departure_local - DEPARTURE_BUFFER - timedelta(minutes=transfer_min)
    return free_from, free_until


def transfer_minutes(pkg: TripPackage) -> int:
    a = KNOWN_AIRPORTS.get(pkg.destination)
    return 60 if not a or a.city_center_km is None else int(20 + a.city_center_km * 1.3)


async def gather_facts(state: ItineraryState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    pkg = state["package"]
    notes: list[str] = []
    offer = pkg.accommodation.offer
    centre: tuple[float, float] | None = None
    pois: list[PointOfInterest] = []
    if deps.poi_provider is None:
        notes.append("OpenStreetMap integracija je isključena – predlozi nisu provereni.")
        return {"centre": None, "pois": [], "notes": notes}
    await deps.emit(
        "itinerary",
        f"Prikupljanje znamenitosti za {pkg.destination_name} (OpenStreetMap)",
        agent="Itinerary Agent",
        tool="find_attractions",
    )
    try:
        query = f"{pkg.destination_name}, {pkg.country}" if pkg.country else pkg.destination_name
        centre = await deps.with_timeout(deps.poi_provider.geocode(query))
        if centre:
            pois = await deps.with_timeout(deps.poi_provider.attractions(centre[0], centre[1], 3000, 40), 60)
    except (ProviderError, TimeoutError, Exception) as exc:  # noqa: BLE001
        log.warning("OSM failed: %s", type(exc).__name__)
        notes.append("OpenStreetMap trenutno nije dostupan – predlozi nisu provereni.")
    if offer.latitude is None:
        notes.append("Lokacija smeštaja nije poznata; udaljenosti su okvirne.")
    return {"centre": centre, "pois": pois, "notes": notes}


ITINERARY_PROMPT = """Ti si Itinerary Agent aplikacije TravelScout.
Napravi realističan plan po danima (srpski, latinica).
Pravila:
- Znamenitosti biraj sa liste "attractions" (i iz alata find_attractions) i za SVAKU stavku kind="sight" navedi
  njen tačan poi_id i naziv kao na listi. Stavke bez poi_id biće prikazane kao neproverene.
- Ako poznate znamenitosti grada nedostaju na listi, pozovi find_attractions sa nazivom kvarta ili znamenitosti.
- Grupiši obilaske po kvartovima da bi se smanjilo putovanje; prioritet su besplatne znamenitosti
  (fee="no" ili nepoznato za trgove/parkove), plaćene navedi kao opcione.
- Poštuj vremenske okvire: prvi dan počinje tek posle dolaska i transfera, poslednji dan se završava pre odlaska na
  aerodrom. Ubaci obroke (ručak, večera), pauze i realno vreme između lokacija.
- U 'days' mora postojati TAČNO po jedan dan za svaki datum iz 'required_dates' (i prvi i poslednji dan).
- Tempo i broj znamenitosti dnevno: {pace}. Interesovanja: {interests}.
- Za transfer aerodrom↔smeštaj opiši opšte opcije (autobus/voz/taksi) sa procenom trajanja; to su procene.
- NE izmišljaj radna vremena, cene ulaznica, ture, linkove ni koordinate. {rule}"""


async def plan_with_llm(state: ItineraryState, deps: RunDeps) -> ItineraryDraft | None:
    assert deps.llm is not None
    pkg = state["package"]
    pois = {p.id: p for p in state.get("pois", [])}
    centre = state.get("centre")

    def poi_brief(p: PointOfInterest) -> dict[str, Any]:
        return {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "fee": p.fee,
            "opening_hours_osm": p.opening_hours,
            "lat": round(p.latitude, 4),
            "lon": round(p.longitude, 4),
        }

    @tool
    async def find_attractions(neighbourhood: str | None = None) -> str:
        """List attractions from OpenStreetMap. Optionally pass a neighbourhood name of the destination city
        to get attractions near it. Returns JSON (id, name, category, fee, opening_hours_osm, lat, lon)."""
        await deps.emit(
            "itinerary",
            f"Itinerary Agent: znamenitosti {neighbourhood or 'centar'}",
            agent="Itinerary Agent",
            tool="find_attractions",
        )
        if neighbourhood and deps.poi_provider:
            try:
                loc = await deps.with_timeout(
                    deps.poi_provider.geocode(f"{neighbourhood[:60]}, {pkg.destination_name}")
                )
                if loc and centre and _km(loc, centre) < 25:
                    extra = await deps.with_timeout(deps.poi_provider.attractions(loc[0], loc[1], 1500, 20), 60)
                    for p in extra:
                        pois.setdefault(p.id, p)
                    return json.dumps([poi_brief(p) for p in extra], ensure_ascii=False)
            except Exception:  # noqa: BLE001
                return json.dumps({"error": "kvart nije pronađen"})
        return json.dumps([poi_brief(p) for p in list(pois.values())[:40]], ensure_ascii=False)

    free_from, free_until = trip_window(pkg, transfer_minutes(pkg))
    offer = pkg.accommodation.offer
    facts = {
        "destination": pkg.destination_name,
        "country": pkg.country,
        "arrival_local": pkg.flight.outbound.arrival_local.isoformat() if pkg.flight.outbound.arrival_local else None,
        "arrival_is_estimate": pkg.flight.outbound.arrival_is_estimate,
        "free_from": free_from.isoformat(timespec="minutes"),
        "return_departure_local": pkg.flight.inbound.departure_local.isoformat(),
        "must_leave_for_airport_by": free_until.isoformat(timespec="minutes"),
        "airport": pkg.destination,
        "accommodation_area": offer.location,
        "accommodation_distance_km_to_centre": str(offer.distance_km) if offer.distance_km is not None else None,
        "travellers": pkg.flight.passengers[0].count if pkg.flight.passengers else None,
        "has_children": any(p.passenger_type == "child" for p in pkg.flight.passengers),
        "required_dates": [
            (free_from.date() + timedelta(days=i)).isoformat()
            for i in range((free_until.date() - free_from.date()).days + 1)
        ],
        "attractions": [poi_brief(p) for p in list(pois.values())[:40]],
    }
    agent = create_agent(
        deps.llm,
        tools=[find_attractions],
        system_prompt=ITINERARY_PROMPT.format(
            pace=f"{state['pace'].value} (~{SIGHTS_PER_DAY[state['pace']]} znamenitosti po punom danu)",
            interests=", ".join(state.get("interests") or []) or "opšte",
            rule=UNTRUSTED_DATA_RULE,
        ),
        response_format=ItineraryDraft,
        middleware=[
            ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"),  # type: ignore[list-item]
            ToolCallLimitMiddleware(run_limit=4),  # type: ignore[list-item]
        ],
        name="itinerary_agent",
    )
    await deps.emit("itinerary", "Itinerary Agent pravi plan", agent="Itinerary Agent")
    out = await asyncio.wait_for(
        agent.ainvoke(
            {"messages": [{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]},
            config={"recursion_limit": 16},
        ),
        timeout=150,
    )
    state["pois"] = list(pois.values())
    draft = out.get("structured_response")
    return draft if isinstance(draft, ItineraryDraft) else None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (a[1] - b[1]) * 111.32 * math.cos(math.radians((a[0] + b[0]) / 2))
    dy = (a[0] - b[0]) * 110.57
    return math.hypot(dx, dy)


def plan_with_rules(state: ItineraryState) -> ItineraryDraft:
    """Deterministic fallback: nearest-neighbour route through OSM attractions."""
    pkg = state["package"]
    pace = state["pace"]
    pois = list(state.get("pois", []))
    free_from, free_until = trip_window(pkg, transfer_minutes(pkg))
    start = state.get("centre") or ((pois[0].latitude, pois[0].longitude) if pois else (0.0, 0.0))
    ordered: list[PointOfInterest] = []
    cur = start
    remaining = pois[:]
    while remaining:
        nxt = min(remaining, key=lambda p: _km(cur, (p.latitude, p.longitude)))
        ordered.append(nxt)
        remaining.remove(nxt)
        cur = (nxt.latitude, nxt.longitude)

    days: list[DraftDay] = []
    day = free_from.date()
    last = free_until.date()
    idx = 0
    while day <= last:
        items: list[DraftItem] = []
        t = datetime.combine(day, time(9, 30))
        end = datetime.combine(day, time(20, 30))
        if day == free_from.date():
            arrival = pkg.flight.outbound.arrival_local or pkg.flight.outbound.departure_local
            items.append(
                DraftItem(
                    start=f"{arrival:%H:%M}",
                    end=f"{free_from:%H:%M}",
                    title="Dolazak i transfer do smeštaja",
                    kind="transfer",
                )
            )
            t = max(t, free_from + timedelta(minutes=30))
        if day == last:
            end = min(end, free_until)
        quota = SIGHTS_PER_DAY[pace] if (end - t) >= timedelta(hours=6) else max(1, SIGHTS_PER_DAY[pace] // 2)
        lunch_done = False
        n = 0
        while idx < len(ordered) and n < quota:
            p = ordered[idx]
            dur = timedelta(minutes=DURATION_BY_CATEGORY.get(p.category, 60))
            if not lunch_done and t.time() >= time(12, 30):
                items.append(
                    DraftItem(start=f"{t:%H:%M}", end=f"{t + timedelta(hours=1):%H:%M}", title="Ručak", kind="meal")
                )
                t += timedelta(hours=1)
                lunch_done = True
            if t + dur > end:
                break
            items.append(
                DraftItem(
                    start=f"{t:%H:%M}",
                    end=f"{t + dur:%H:%M}",
                    title=p.name,
                    kind="sight",
                    poi_id=p.id,
                    paid=(p.fee == "yes") if p.fee else None,
                )
            )
            t += dur + timedelta(minutes=20)
            idx += 1
            n += 1
        if day != last and t < datetime.combine(day, time(19, 30)) <= end:
            items.append(DraftItem(start="19:30", end="21:00", title="Večera", kind="meal"))
        if day == last:
            items.append(
                DraftItem(
                    start=f"{free_until:%H:%M}",
                    end=f"{pkg.flight.inbound.departure_local:%H:%M}",
                    title="Transfer do aerodroma i odlazak",
                    kind="transfer",
                )
            )
        if not any(i.kind == "sight" for i in items) and day not in (free_from.date(), last):
            items.insert(
                0, DraftItem(start="10:00", end="17:00", title="Slobodno vreme / šetnja centrom", kind="free_time")
            )
        days.append(DraftDay(date=day.isoformat(), title=f"Dan {len(days) + 1}", items=items))
        day += timedelta(days=1)
    return ItineraryDraft(days=days, transfers=[])


def fill_missing_days(draft: ItineraryDraft, state: ItineraryState) -> ItineraryDraft:
    """The model sometimes skips days; fill any missing trip day from the rule-based planner,
    using attractions the model has not already scheduled."""
    have = {d.date for d in draft.days}
    used = {i.poi_id for d in draft.days for i in d.items if i.poi_id}
    rest = [p for p in state.get("pois", []) if p.id not in used]
    rules = plan_with_rules({**state, "pois": rest})  # type: ignore[typeddict-item]
    missing = [d for d in rules.days if d.date not in have]
    if not missing:
        return draft
    return draft.model_copy(update={"days": sorted([*draft.days, *missing], key=lambda d: d.date)})


async def plan(state: ItineraryState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    notes = list(state.get("notes", []))
    if deps.llm is not None:
        try:
            draft = await plan_with_llm(state, deps)
            if draft and draft.days:
                draft = fill_missing_days(draft, state)
                return {"draft": draft, "used_llm": True, "pois": state.get("pois", []), "notes": notes}
        except Exception as exc:  # noqa: BLE001
            log.warning("itinerary agent failed: %s", type(exc).__name__)
            notes.append("Itinerary Agent (LLM) nije uspeo; korišćen je plan na osnovu pravila.")
    else:
        notes.append("Bez OpenAI ključa: plan je napravljen pravilima (najbliži sledeći objekat).")
    await deps.emit("itinerary", "Plan na osnovu pravila", agent="Itinerary Agent")
    return {"draft": plan_with_rules(state), "used_llm": False, "notes": notes}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def match_poi(title: str, pois: dict[str, PointOfInterest]) -> PointOfInterest | None:
    """Link a free-text plan item to a real OSM POI by normalized name containment (len >= 5)."""
    t = _norm(title)
    best: PointOfInterest | None = None
    for p in pois.values():
        n = _norm(p.name)
        if len(n) >= 5 and (n in t or (len(t) >= 5 and t in n)):
            if best is None or len(n) > len(_norm(best.name)):
                best = p
    return best


def _valid_hhmm(s: str) -> str:
    try:
        return datetime.strptime(s.strip()[:5], "%H:%M").strftime("%H:%M")
    except ValueError:
        return "--:--"


async def finalize(state: ItineraryState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    pkg = state["package"]
    draft = state.get("draft") or ItineraryDraft(days=[])
    pois = {p.id: p for p in state.get("pois", [])}
    free_from, free_until = trip_window(pkg, transfer_minutes(pkg))
    valid_days = {free_from.date() + timedelta(days=i) for i in range((free_until.date() - free_from.date()).days + 1)}
    tier = ESTIMATES[cost_tier(next((a.country_code for a in [KNOWN_AIRPORTS.get(pkg.destination)] if a), None))]
    people = sum(p.count for p in pkg.flight.passengers if p.passenger_type != "infant")
    used_pois: dict[str, PointOfInterest] = {}

    days: list[ItineraryDay] = []
    for d in draft.days:
        try:
            day = date.fromisoformat(d.date)
        except ValueError:
            continue
        if day not in valid_days:
            continue
        items = []
        paid = 0
        for it in d.items:
            poi = pois.get(it.poi_id or "")
            if poi is None and it.kind == "sight":
                poi = match_poi(it.title, pois)
            if poi:
                used_pois[poi.id] = poi
            if it.paid:
                paid += 1
            items.append(
                ItineraryItem(
                    start=_valid_hhmm(it.start),
                    end=_valid_hhmm(it.end),
                    title=it.title[:120],
                    kind=it.kind,
                    poi_id=poi.id if poi else None,
                    area=it.area,
                    description=(it.description or "")[:300] or None,
                    paid=it.paid if poi is None else (poi.fee == "yes" if poi.fee else it.paid),
                    map_url=poi.map_url
                    if poi
                    else (
                        google_maps_search_url(it.title, *(state.get("centre") or (0, 0)))
                        if it.kind == "sight" and state.get("centre")
                        else None
                    ),
                    source_url=(poi.website or poi.wikipedia) if poi else None,
                    opening_hours=poi.opening_hours if poi else None,
                    verified=poi is not None,
                )
            )
        days.append(
            ItineraryDay(
                date=day,
                title=d.title[:80],
                area=d.area,
                items=items,
                est_sightseeing_eur=q(tier["activities"] * people * Decimal(min(paid, 2)) / 2) if paid else Decimal(0),
                est_transport_eur=q(tier["transport"] * people),
            )
        )
    days.sort(key=lambda x: x.date)

    offer = pkg.accommodation.offer
    airport = KNOWN_AIRPORTS.get(pkg.destination)
    directions = None
    if offer.latitude is not None and offer.longitude is not None:
        a_lat, a_lon = (airport.lat, airport.lon) if airport else (None, None)
        if a_lat is not None:
            directions = (
                "https://www.google.com/maps/dir/?api=1&travelmode=transit"
                f"&origin={a_lat},{a_lon}&destination={offer.latitude},{offer.longitude}"
            )
    transfers = [
        TransferOption(
            mode=t.mode[:40],
            description=t.description[:300],
            est_duration_min=t.est_duration_min,
            est_cost_eur=q(Decimal(str(t.est_cost_eur))) if t.est_cost_eur is not None else None,
            verified=False,
            source_url=directions,
        )
        for t in draft.transfers[:5]
    ]
    if not transfers:
        transfers.append(
            TransferOption(
                mode="Javni prevoz / šatl",
                description="Proverite aktuelne linije i cene na linku za rutu.",
                est_duration_min=transfer_minutes(pkg),
                verified=False,
                source_url=directions,
            )
        )

    disclaimers = [
        "Znamenitosti, koordinate i oznake radnog vremena/ulaznica potiču iz OpenStreetMap-a (zajednički podaci) – "
        "proverite aktuelno radno vreme i cene na zvaničnim sajtovima.",
        "Trajanja obilazaka, vreme putovanja i cene transfera su procene, nisu provereni podaci.",
    ]
    if pkg.flight.outbound.arrival_is_estimate:
        disclaimers.append("Vreme sletanja je procena – prvi dan prilagodite stvarnom vremenu leta.")
    disclaimers += state.get("notes", [])
    sources = sorted({p.source for p in used_pois.values()} | ({"Google Maps (link za rutu)"} if directions else set()))
    itinerary = Itinerary(
        package_id=pkg.id,
        destination_name=pkg.destination_name,
        days=days,
        transfers=transfers,
        pois=list(used_pois.values()),
        sources=sources,
        disclaimers=disclaimers,
        used_llm=bool(state.get("used_llm")),
        generated_at=datetime.now(UTC),
    )
    await deps.emit("itinerary", "Plan je spreman", "done", agent="Itinerary Agent")
    return {"itinerary": itinerary}


def build_itinerary_graph() -> Any:
    g = StateGraph(ItineraryState)
    g.add_node("gather_facts", gather_facts)
    g.add_node("plan", plan)
    g.add_node("finalize", finalize)
    g.add_edge(START, "gather_facts")
    g.add_edge("gather_facts", "plan")
    g.add_edge("plan", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


ITINERARY_GRAPH = build_itinerary_graph()


async def generate_itinerary(pkg: TripPackage, interests: list[str], pace: Pace, deps: RunDeps) -> Itinerary:
    out = await ITINERARY_GRAPH.ainvoke(
        {"package": pkg, "interests": interests, "pace": pace},
        config={"configurable": {"deps": deps}, "recursion_limit": 10},
    )
    return out["itinerary"]  # type: ignore[no-any-return]
