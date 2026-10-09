"""LangGraph search workflow.

validate_request → discover_flights → shortlist_trips → select_flights (human-in-the-loop interrupt)
→ (fan-out) search_accommodation → validate_eligibility → calculate_totals → rank_packages

In interactive mode the graph pauses after the flight search and shows every flight/date
combination; accommodation is searched only for the flights the user picks.

Arithmetic, filtering, dates, currency and ranking are plain Python. Agents are used for
request interpretation (before the graph), flight-search orchestration and explanations.
Itineraries are generated on demand by a separate graph (see itinerary_graph.py).
"""

from __future__ import annotations

import operator
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from app.agents.accommodation_agent import TripAccommodation, explain_packages, search_for_trip
from app.agents.context import RunDeps
from app.agents.flight_agent import discover_flights
from app.domain.airports import KNOWN_AIRPORTS
from app.domain.models import (
    AssessedOffer,
    Diagnostic,
    EligibilityStatus,
    FlightOption,
    Money,
    SearchConstraints,
    TripPackage,
)
from app.providers.base import DestinationInfo
from app.services.costing import build_cost_breakdown, within_budget
from app.services.eligibility import RULE_MESSAGES, evaluate_offer
from app.services.ranking import rank_packages
from app.services.timeplan import stay_dates, usable_time

MAX_PER_DESTINATION = 2
MAX_FLIGHT_CHOICES = 80
MAX_NEEDS_VERIFICATION = 30


class SearchState(TypedDict, total=False):
    constraints: SearchConstraints
    flight_options: list[FlightOption]
    destinations: dict[str, DestinationInfo]
    shortlist: list[FlightOption]
    flight_choices: list[FlightOption]
    accommodation_results: Annotated[list[TripAccommodation], operator.add]
    selections: dict[str, AssessedOffer]
    needs_verification: list[AssessedOffer]
    rejected_counts: dict[str, int]
    packages: list[TripPackage]
    diagnostics: Annotated[list[Diagnostic], operator.add]
    agent_notes: Annotated[list[str], operator.add]
    suggestions: list[str]
    used_llm: bool
    stop: bool


class TripState(TypedDict):
    trip: FlightOption
    destination: DestinationInfo
    constraints: SearchConstraints


def _deps(config: RunnableConfig) -> RunDeps:
    return config["configurable"]["deps"]  # type: ignore[index, no-any-return]


# --- nodes ----------------------------------------------------------------------------------


async def validate_request(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    c = SearchConstraints.model_validate(state["constraints"].model_dump())
    await deps.emit("validate", "Provera ograničenja pretrage", agent="Travel Coordinator")
    _, last = c.outbound_dates
    if last < date.today():
        await deps.emit("validate", "Period polaska je u prošlosti", "error")
        return {
            "constraints": c,
            "stop": True,
            "diagnostics": [
                Diagnostic(stage="validate", code="dates_in_past", message="Ceo period polaska je u prošlosti.")
            ],
        }
    await deps.emit("validate", "Ograničenja su validna", "done", agent="Travel Coordinator")
    return {"constraints": c, "stop": False}


async def discover_flights_node(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    await deps.emit("flights", "Flight Agent traži Wizz Air letove", agent="Flight Agent")
    disc = await discover_flights(state["constraints"], deps)
    await deps.emit(
        "flights",
        f"Pronađeno {len(disc.options)} kombinacija letova za {len(disc.destinations)} destinacija",
        "done",
        agent="Flight Agent",
    )
    return {
        "flight_options": disc.options,
        "destinations": disc.destinations,
        "diagnostics": disc.diagnostics,
        "agent_notes": disc.notes,
        "used_llm": disc.used_llm,
    }


async def shortlist_trips(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    c = state["constraints"]
    by_dest: dict[str, list[FlightOption]] = defaultdict(list)
    over_budget = 0
    for o in state.get("flight_options", []):
        if c.budget_amount is not None:
            budget = deps.fx.convert(Money(amount=c.budget_amount, currency=c.budget_currency), "EUR").amount
            fare = o.total_fare.amount / (c.party.seated_passengers if c.budget_scope.value == "per_person" else 1)
            if fare > budget:
                over_budget += 1
                continue
        by_dest[o.outbound.destination].append(o)
    picked: list[FlightOption] = []
    for opts in by_dest.values():
        opts.sort(key=lambda o: o.total_fare.amount)
        seen_dates: set[tuple[date, date]] = set()
        for o in opts:
            key = (o.outbound.departure_local.date(), o.inbound.departure_local.date())
            if key in seen_dates:
                continue
            seen_dates.add(key)
            picked.append(o)
            if len(seen_dates) >= MAX_PER_DESTINATION:
                break
    picked.sort(key=lambda o: o.total_fare.amount)
    shortlist = picked[: deps.settings.max_shortlist]
    diags = []
    if over_budget:
        diags.append(
            Diagnostic(
                stage="shortlist",
                code="flights_over_budget",
                count=over_budget,
                message="Kombinacije letova čija sama cena prelazi budžet su isključene.",
            )
        )
    # Every combination within the constraints, grouped by destination then by date, for the user to pick from.
    choices = sorted(
        [o for opts in by_dest.values() for o in opts],
        key=lambda o: (o.outbound.destination, o.outbound.departure_local, o.inbound.departure_local),
    )[:MAX_FLIGHT_CHOICES]
    await deps.emit("shortlist", f"Uži izbor: {len(shortlist)} putovanja (limit {deps.settings.max_shortlist})", "done")
    return {"shortlist": shortlist, "flight_choices": choices, "diagnostics": diags}


async def select_flights(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    """Human-in-the-loop: pause and let the user choose which flights get an accommodation search."""
    deps = _deps(config)
    if not config["configurable"].get("interactive") or state.get("stop") or not state.get("flight_choices"):
        return {}
    await deps.emit("select", "Izaberite letove za koje tražimo smeštaj", "done")
    picked = interrupt({"kind": "select_flights", "max": deps.settings.max_shortlist})
    by_id = {o.id: o for o in state.get("flight_choices", [])}
    chosen = [by_id[i] for i in dict.fromkeys(picked or []) if i in by_id][: deps.settings.max_shortlist]
    await deps.emit("select", f"Izabrano letova: {len(chosen)}", "done")
    return {"shortlist": chosen}


def fan_out_accommodation(state: SearchState) -> list[Send] | str:
    if state.get("stop") or not state.get("shortlist"):
        return "rank_packages"
    dests = state.get("destinations", {})
    sends = []
    for trip in state["shortlist"]:
        d = dests.get(trip.outbound.destination) or DestinationInfo(
            iata=trip.outbound.destination, name=trip.outbound.destination
        )
        sends.append(
            Send("search_accommodation", {"trip": trip, "destination": d, "constraints": state["constraints"]})
        )
    return sends


def after_validate(state: SearchState) -> str:
    return "rank_packages" if state.get("stop") else "discover_flights"


async def search_accommodation(state: TripState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    res = await search_for_trip(state["trip"], state["destination"], state["constraints"], deps)
    return {"accommodation_results": [res]}


async def validate_eligibility(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    c = state["constraints"]
    selections: dict[str, AssessedOffer] = {}
    needs: list[AssessedOffer] = []
    rejected: Counter[str] = Counter()
    seen_needs: set[str] = set()
    for res in state.get("accommodation_results", []):
        eligible: list[AssessedOffer] = []
        for offer in res.offers:
            er = evaluate_offer(offer, c, res.checkin, res.checkout)
            assessed = AssessedOffer(offer=offer, eligibility=er)
            if er.status is EligibilityStatus.ELIGIBLE:
                eligible.append(assessed)
            elif er.status is EligibilityStatus.NEEDS_VERIFICATION:
                if offer.id not in seen_needs:
                    seen_needs.add(offer.id)
                    needs.append(assessed)
            else:
                rejected.update(er.failed)
        if eligible:
            best = min(
                eligible,
                key=lambda a: (
                    deps.fx.convert(a.offer.total_price, "EUR").amount  # type: ignore[arg-type]
                    if a.offer.total_price
                    else Decimal("Infinity")
                ),
            )
            selections[res.trip_id] = best
    await deps.emit(
        "eligibility",
        f"Ispunjava uslove: {len(selections)} putovanja; za proveru: {len(needs)}; "
        f"odbijeno ponuda: {sum(rejected.values())}",
        "done",
        agent="Accommodation Agent",
    )
    return {
        "selections": selections,
        "needs_verification": needs[:MAX_NEEDS_VERIFICATION],
        "rejected_counts": dict(rejected),
    }


def _transfer_minutes(dest: DestinationInfo | None) -> int:
    km = dest.airport_to_centre_km if dest and dest.airport_to_centre_km is not None else None
    return 60 if km is None else int(20 + km * 1.3)


async def calculate_totals(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    c = state["constraints"]
    dests = state.get("destinations", {})
    packages: list[TripPackage] = []
    over_budget = 0
    for trip in state.get("shortlist", []):
        sel = state.get("selections", {}).get(trip.id)
        if sel is None:
            continue
        dest = dests.get(trip.outbound.destination)
        ut = usable_time(trip.outbound, trip.inbound, _transfer_minutes(dest))
        checkin, checkout, _ = stay_dates(trip.outbound, trip.inbound)
        trip_days = (trip.inbound.departure_local.date() - trip.outbound.departure_local.date()).days + 1
        known = KNOWN_AIRPORTS.get(trip.outbound.destination)
        cost = build_cost_breakdown(
            trip,
            sel.offer,
            c,
            deps.fx,
            country_code=(dest.country_code if dest else None) or (known.country_code if known else None),
            airport_to_centre_km=dest.airport_to_centre_km if dest else None,
            trip_days=trip_days,
        )
        if not within_budget(cost, c, deps.fx):
            over_budget += 1
            continue
        origins = sorted({trip.evidence.origin, sel.offer.evidence.origin}, key=lambda o: o.value)
        packages.append(
            TripPackage(
                id=f"pkg-{trip.id}-{sel.offer.id}"[:80],
                destination=trip.outbound.destination,
                destination_name=((dest.city or dest.name) if dest else trip.outbound.destination),
                country=dest.country if dest else None,
                flight=trip,
                accommodation=sel,
                cost=cost,
                usable_time=ut,
                data_origins=origins,
            )
        )
    diags = []
    if over_budget:
        diags.append(
            Diagnostic(
                stage="totals",
                code="over_budget",
                count=over_budget,
                message="Paketi čiji poznati troškovi prelaze budžet su isključeni.",
            )
        )
    await deps.emit("totals", f"Izračunati troškovi za {len(packages)} paketa", "done")
    return {"packages": packages, "diagnostics": diags}


async def rank_packages_node(state: SearchState, config: RunnableConfig) -> dict[str, Any]:
    deps = _deps(config)
    c = state["constraints"]
    ranked = rank_packages(state.get("packages", []), c.ranking)
    if ranked:
        await deps.emit("rank", "Accommodation Agent piše objašnjenja", agent="Accommodation Agent")
        notes = await explain_packages(ranked, deps)
        ranked = [
            p.model_copy(update={"highlights": p.highlights + ([f"Agent: {notes[p.id]}"] if p.id in notes else [])})
            for p in ranked
        ]
    suggestions = build_suggestions(state, ranked)
    await deps.emit("rank", f"Rangirano {len(ranked)} paketa", "done")
    return {"packages": ranked, "suggestions": suggestions}


def build_suggestions(state: SearchState, ranked: list[TripPackage]) -> list[str]:
    """Explain what blocked matches and suggest changes. Never applied automatically."""
    if ranked:
        return []
    c = state.get("constraints")
    s: list[str] = []
    if state.get("stop"):
        return ["Izaberite period polaska u budućnosti."]
    if not state.get("flight_options"):
        s.append("Nisu pronađeni letovi: proširite period polaska ili raspon noći.")
        if c and len(c.origins) == 1:
            s.append("Dodajte još polaznih aerodroma (npr. INI, BUD, TSR).")
        if c and not c.anywhere:
            s.append("Uključite opciju „Bilo gde“ da biste videli sve destinacije.")
        return s
    if not state.get("shortlist"):
        s.append("Sve kombinacije letova prelaze budžet – razmotrite veći budžet ili drugi period.")
        return s
    rejected = state.get("rejected_counts", {})
    for code, n in sorted(rejected.items(), key=lambda kv: -kv[1]):
        msg = RULE_MESSAGES.get(code, code)
        hint = {
            "occupancy_insufficient": " → Razmotrite više soba (polje „Broj soba“).",
            "too_far": " → Povećajte maksimalnu udaljenost od centra.",
            "review_score_below_min": " (obavezni uslov – ne menja se automatski)",
            "hostel": " (obavezni uslov)",
            "shared_bathroom": " (obavezni uslov)",
            "shared_sleeping_space": " (obavezni uslov)",
        }.get(code, "")
        s.append(f"{n}× odbijeno: {msg}{hint}")
    if state.get("needs_verification"):
        s.append(f"{len(state['needs_verification'])} ponuda čeka ručnu proveru (sekcija „Potrebna provera“).")
    if any(d.code == "over_budget" for d in state.get("diagnostics", [])):
        s.append("Neki paketi su isključeni zbog budžeta – razmotrite veći budžet.")
    if not any(r.offers for r in state.get("accommodation_results", [])):
        s.append("Nijedan izvor smeštaja nije vratio ponude – proverite panel Integracije ili uvezite smeštaj ručno.")
    return s


def build_search_graph(checkpointer: InMemorySaver | None = None) -> Any:
    g = StateGraph(SearchState)
    g.add_node("validate_request", validate_request)
    g.add_node("discover_flights", discover_flights_node)
    g.add_node("shortlist_trips", shortlist_trips)
    g.add_node("select_flights", select_flights)
    g.add_node("search_accommodation", search_accommodation)
    g.add_node("validate_eligibility", validate_eligibility)
    g.add_node("calculate_totals", calculate_totals)
    g.add_node("rank_packages", rank_packages_node)

    g.add_edge(START, "validate_request")
    g.add_conditional_edges("validate_request", after_validate, ["discover_flights", "rank_packages"])
    g.add_edge("discover_flights", "shortlist_trips")
    g.add_edge("shortlist_trips", "select_flights")
    g.add_conditional_edges("select_flights", fan_out_accommodation, ["search_accommodation", "rank_packages"])
    g.add_edge("search_accommodation", "validate_eligibility")
    g.add_edge("validate_eligibility", "calculate_totals")
    g.add_edge("calculate_totals", "rank_packages")
    g.add_edge("rank_packages", END)
    return g.compile(checkpointer=checkpointer)


# Automatic (no pause) graph, used by tests and scripted runs.
SEARCH_GRAPH = build_search_graph()


# Interactive graph: checkpoints let a paused search resume after the user picks flights.
def _checkpoint_types() -> list[tuple[str, str]]:
    """Explicit allowlist of our own types that may be restored from checkpoints."""
    import enum

    from pydantic import BaseModel

    from app.domain import models

    allowed = [
        (models.__name__, name)
        for name, obj in vars(models).items()
        if isinstance(obj, type) and issubclass(obj, (BaseModel, enum.Enum)) and obj.__module__ == models.__name__
    ]
    return [
        *allowed,
        (DestinationInfo.__module__, "DestinationInfo"),
        (TripAccommodation.__module__, "TripAccommodation"),
    ]


INTERACTIVE_SEARCH_GRAPH = build_search_graph(
    InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=_checkpoint_types()))
)
