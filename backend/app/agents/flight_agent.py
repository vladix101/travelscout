"""Flight Agent (Wizz Air).

The LLM orchestrates two narrowly-scoped tools (list destinations, search round trips).
Tools write structured `FlightOption`s into a run registry; the LLM only ever sees compact
summaries and can never create or alter prices. After the agent finishes, a deterministic
coverage check searches any requested destination the agent skipped, so destinations are
never silently dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agents.context import RunDeps
from app.agents.llm import UNTRUSTED_DATA_RULE
from app.domain.models import Diagnostic, FlightOption, SearchConstraints
from app.providers.base import DestinationInfo, ProviderBlockedError, ProviderError

log = logging.getLogger(__name__)


class FlightAgentReport(BaseModel):
    searched_destinations: list[str] = Field(description="IATA codes searched")
    notes: str = Field(description="Kratko objašnjenje na srpskom (max 3 rečenice), bez izmišljenih cena")


@dataclass
class FlightDiscovery:
    options: list[FlightOption] = field(default_factory=list)
    destinations: dict[str, DestinationInfo] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    used_llm: bool = False


class FlightSearchRunner:
    """Deterministic tool implementations shared by the LLM agent and the rule-based path."""

    def __init__(self, constraints: SearchConstraints, deps: RunDeps):
        self.c = constraints
        self.deps = deps
        self.registry: dict[str, FlightOption] = {}
        self.searched: set[tuple[str, str]] = set()
        self.routes: dict[str, dict[str, DestinationInfo]] = {}
        self.failures: dict[str, list[str]] = defaultdict(list)
        self.successes: dict[str, int] = defaultdict(int)
        self._sem = asyncio.Semaphore(deps.settings.provider_concurrency)

    async def list_destinations(self, origin: str) -> dict[str, DestinationInfo]:
        if origin in self.routes:
            return self.routes[origin]
        found: dict[str, DestinationInfo] = {}
        for p in self.deps.flight_providers:
            try:
                for d in await self.deps.with_timeout(p.list_destinations(origin, self.c.direct_only)):
                    found.setdefault(d.iata, d)
                    if d.name != d.iata or d.iata not in self.deps.destinations:
                        self.deps.destinations[d.iata] = d
            except (ProviderError, TimeoutError) as exc:
                self.failures[p.name].append(f"rute {origin}: {exc or 'timeout'}")
            except Exception as exc:  # noqa: BLE001
                log.exception("route listing failed")
                self.failures[p.name].append(f"rute {origin}: {type(exc).__name__}")
        self.routes[origin] = found
        return found

    async def _search_one(self, origin: str, dest: DestinationInfo) -> int:
        if (origin, dest.iata) in self.searched:
            return 0
        self.searched.add((origin, dest.iata))
        count = 0
        async with self._sem:
            for p in self.deps.flight_providers:
                try:
                    opts = await self.deps.with_timeout(p.search_round_trips(self.c, origin, dest))
                    for o in opts:
                        self.registry[o.id] = o
                    count += len(opts)
                    self.successes[p.name] += 1
                except ProviderBlockedError as exc:
                    self.failures[p.name].append(f"{origin}-{dest.iata}: {exc}")
                except (ProviderError, TimeoutError) as exc:
                    self.failures[p.name].append(f"{origin}-{dest.iata}: {exc or 'timeout'}")
                except Exception as exc:  # noqa: BLE001
                    log.exception("flight search failed")
                    self.failures[p.name].append(f"{origin}-{dest.iata}: {type(exc).__name__}")
        return count

    def target_destinations(self, origin: str) -> list[DestinationInfo]:
        routes = self.routes.get(origin, {})
        if self.c.anywhere:
            return list(routes.values())
        return [routes[d] for d in self.c.destinations if d in routes]

    async def search(self, origin: str, codes: list[str]) -> list[dict]:
        routes = await self.list_destinations(origin)
        dests = [routes[c] for c in codes if c in routes]
        await self.deps.emit(
            "flights",
            f"Pretraga letova {origin} → {len(dests)} destinacija",
            agent="Flight Agent",
            tool="search_round_trips",
        )
        await asyncio.gather(*(self._search_one(origin, d) for d in dests))
        return self.summary(origin, codes)

    def summary(self, origin: str, codes: list[str]) -> list[dict]:
        out = []
        for code in codes:
            opts = [o for o in self.registry.values() if o.outbound.origin == origin and o.outbound.destination == code]
            if not opts:
                out.append({"destination": code, "options": 0})
                continue
            best = min(opts, key=lambda o: o.total_fare.amount)
            out.append(
                {
                    "destination": code,
                    "options": len(opts),
                    "cheapest_option_id": best.id,
                    "cheapest_total_eur": str(best.total_fare.amount),
                    "dates": f"{best.outbound.departure_local:%Y-%m-%d} → {best.inbound.departure_local:%Y-%m-%d}",
                }
            )
        return out

    def finish(self) -> list[Diagnostic]:
        diags: list[Diagnostic] = []
        for p in self.deps.flight_providers:
            fails = self.failures.get(p.name, [])
            ok = self.successes.get(p.name, 0)
            origin_kind = getattr(p, "used_cache", False)
            if fails and ok:
                self.deps.record(p.name, "partial", f"{len(fails)} neuspelih zahteva; npr. {fails[0]}")
            elif fails:
                self.deps.record(p.name, "failed", fails[0])
            else:
                self.deps.record(p.name, "ok", f"{ok} pretraga" + (" (deo iz keša)" if origin_kind else ""))
        if self.failures:
            diags.append(
                Diagnostic(
                    stage="flights",
                    code="provider_errors",
                    message="Neki zahtevi za letove nisu uspeli; rezultati mogu biti nepotpuni.",
                    count=sum(len(v) for v in self.failures.values()),
                )
            )
        return diags


FLIGHT_SYSTEM_PROMPT = """Ti si Flight Agent aplikacije TravelScout, specijalizovan za Wizz Air povratne letove.
Ograničenja pretrage su fiksna i već su primenjena u alatima (datumi, broj noći, putnici, direktni letovi).
Postupak:
1. Za svaki polazni aerodrom pozovi list_wizz_destinations.
2. Pozovi search_round_trips za polazni aerodrom i listu destinacija: ako je "anywhere"=true, pretraži SVE
   destinacije iz liste (jednim pozivom po aerodromu); inače samo tražene destinacije koje postoje u listi.
3. Završi kratkim izveštajem (činjenice, bez pitanja korisniku i bez ponude daljih radnji).
   Cene navodi samo ako su vraćene iz alata.
{rule}"""


async def discover_flights(constraints: SearchConstraints, deps: RunDeps) -> FlightDiscovery:
    runner = FlightSearchRunner(constraints, deps)
    result = FlightDiscovery()

    if deps.llm is not None:

        @tool
        async def list_wizz_destinations(origin: str) -> str:
            """List destinations served from a departure airport (IATA code). Returns JSON."""
            origin = origin.strip().upper()
            if origin not in constraints.origins:
                return json.dumps({"error": f"{origin} nije među izabranim polaznim aerodromima"})
            await deps.emit(
                "flights", f"Lista destinacija iz {origin}", agent="Flight Agent", tool="list_wizz_destinations"
            )
            routes = await runner.list_destinations(origin)
            return json.dumps(
                [{"iata": d.iata, "name": d.name, "country": d.country} for d in routes.values()], ensure_ascii=False
            )

        @tool
        async def search_round_trips(origin: str, destinations: list[str]) -> str:
            """Search Wizz Air round trips from `origin` to the given destination IATA codes (max 40).
            Returns JSON summaries; prices are provider data, never estimates."""
            origin = origin.strip().upper()
            if origin not in constraints.origins:
                return json.dumps({"error": f"{origin} nije među izabranim polaznim aerodromima"})
            codes = [d.strip().upper() for d in destinations][:40]
            if not constraints.anywhere:
                codes = [c for c in codes if c in constraints.destinations]
            return json.dumps(await runner.search(origin, codes), ensure_ascii=False)

        agent = create_agent(
            deps.llm,
            tools=[list_wizz_destinations, search_round_trips],
            system_prompt=FLIGHT_SYSTEM_PROMPT.format(rule=UNTRUSTED_DATA_RULE),
            response_format=FlightAgentReport,
            middleware=[
                ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),  # type: ignore[list-item]
                ToolCallLimitMiddleware(run_limit=4 + 2 * len(constraints.origins)),  # type: ignore[list-item]
            ],
            name="flight_agent",
        )
        request = {
            "origins": constraints.origins,
            "anywhere": constraints.anywhere,
            "destinations": constraints.destinations,
            "interests": constraints.interests,
        }
        await deps.emit("flights", "Flight Agent planira pretragu", agent="Flight Agent")
        try:
            out = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]},
                    config={"recursion_limit": 20},
                ),
                timeout=deps.settings.max_job_seconds / 2,
            )
            report = out.get("structured_response")
            if isinstance(report, FlightAgentReport) and report.notes:
                result.notes.append(f"Flight Agent: {report.notes}")
            result.used_llm = True
        except Exception as exc:  # noqa: BLE001
            log.warning("flight agent failed, falling back to rules: %s", type(exc).__name__)
            result.notes.append("Flight Agent (LLM) nije završio; korišćena je pravila-bazirana pretraga.")

    # Deterministic path / coverage guarantee.
    for origin in constraints.origins:
        await runner.list_destinations(origin)
        targets = runner.target_destinations(origin)
        missing = [d for d in targets if (origin, d.iata) not in runner.searched]
        if missing:
            await deps.emit(
                "flights",
                f"Pretraga letova {origin} → {len(missing)} destinacija",
                agent="Flight Agent",
                tool="search_round_trips",
            )
            await asyncio.gather(*(runner._search_one(origin, d) for d in missing))
        if not constraints.anywhere:
            for code in constraints.destinations:
                if runner.routes.get(origin) is not None and code not in runner.routes[origin]:
                    result.diagnostics.append(
                        Diagnostic(
                            stage="flights",
                            code="route_not_served",
                            message=f"Nema direktne Wizz Air linije {origin} → {code} u dostupnim podacima.",
                        )
                    )

    result.diagnostics += runner.finish()
    result.options = sorted(runner.registry.values(), key=lambda o: o.total_fare.amount)
    result.destinations = {
        code: deps.destinations[code]
        for code in {o.outbound.destination for o in result.options}
        if code in deps.destinations
    }
    return result
