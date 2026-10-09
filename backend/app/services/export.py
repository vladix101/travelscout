"""Markdown / JSON export of a selected trip package (and its itinerary, if generated)."""

from __future__ import annotations

import json
from typing import Any

from app.domain.models import Itinerary, TripPackage

ORIGIN_LABEL = {"live": "UŽIVO", "cached": "KEŠ", "manual": "RUČNI UNOS", "demo": "DEMO", "unverified": "NEPROVERENO"}
BATHROOM_LABEL = {
    "ensuite": "Privatno, u sobi (en-suite)",
    "private": "Privatno (lokacija nije navedena)",
    "private_external": "Privatno, VAN sobe",
    "entire_unit": "Ceo smeštaj – sopstveno kupatilo",
    "shared": "Zajedničko",
    "unknown": "Nepoznato",
}


def _fmt_dt(dt: Any) -> str:
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "nepoznato"


def package_to_markdown(pkg: TripPackage, itinerary: Itinerary | None = None) -> str:
    f, o = pkg.flight, pkg.accommodation.offer
    cur = pkg.cost.currency
    lines = [
        f"# TravelScout – {pkg.destination_name}{f', {pkg.country}' if pkg.country else ''}",
        "",
        f"Izvori podataka: {', '.join(ORIGIN_LABEL[x.value] for x in pkg.data_origins)}",
        "",
        "## Letovi (Wizz Air)",
        "",
        "| Pravac | Polazak | Dolazak | Let | Cena po sedištu |",
        "|---|---|---|---|---|",
    ]
    for label, leg in (("Tamo", f.outbound), ("Nazad", f.inbound)):
        arr = _fmt_dt(leg.arrival_local) + (" (procena)" if leg.arrival_is_estimate and leg.arrival_local else "")
        lines.append(
            f"| {label} {leg.origin}→{leg.destination} | {_fmt_dt(leg.departure_local)} {leg.departure_tz or ''} "
            f"| {arr} {leg.arrival_tz or ''} | {leg.flight_number or '—'} "
            f"| {leg.fare_per_seat.amount:,.2f} {leg.fare_per_seat.currency} |"
        )
    lines += [
        "",
        f"- Uključen prtljag: {f.included_baggage}",
        f"- Tarifa: {'redovna' if f.fare_type.value == 'regular' else 'članska (Discount Club)'}",
        f"- Izvor: {f.evidence.provider}, preuzeto {_fmt_dt(f.evidence.retrieved_at)} UTC",
    ]
    if f.booking_url:
        lines.append(f"- Link: {f.booking_url}")
    lines += [
        "",
        "## Smeštaj",
        "",
        f"**{o.property_name}** ({o.property_type or 'tip nepoznat'}) – {o.location or ''}",
        "",
        f"- Ocena: {o.review_score if o.review_score is not None else 'nepoznata'}"
        f"{f' ({o.review_count} recenzija)' if o.review_count else ''}",
        f"- Soba: {o.room_name or 'nepoznato'}; kupatilo: {BATHROOM_LABEL[o.bathroom.value]}"
        f" – {o.bathroom_evidence or ''}",
        f"- Boravak: {o.checkin:%d.%m.%Y} → {o.checkout:%d.%m.%Y} ({o.nights} noći, {o.rooms} soba)",
        f"- Otkazivanje: {o.cancellation or 'nepoznato'}; plaćanje: {o.prepayment or 'nepoznato'}",
    ]
    if o.distance_km is not None:
        lines.append(f"- Udaljenost: {o.distance_km} km od {o.distance_reference or 'centra'}")
    if o.source_url:
        lines.append(f"- Link: {o.source_url}")
    lines += ["", "## Troškovi", "", "| Stavka | Iznos | Vrsta | Napomena |", "|---|---|---|---|"]
    kinds = {"mandatory": "obavezno", "extra": "dodatak", "estimate": "procena", "unknown": "NEPOZNATO"}
    for ln in pkg.cost.lines:
        amt = f"{ln.amount.amount:,.2f} {ln.amount.currency}" if ln.amount else "?"
        lines.append(f"| {ln.label} | {amt} | {kinds[ln.category]} | {ln.note or ''} |")
    lines += [
        "",
        f"- **Poznati troškovi:** {pkg.cost.known_total:,.2f} {cur} ({pkg.cost.per_person_known:,.2f} {cur} po osobi)",
        f"- Procene: {pkg.cost.estimated:,.2f} {cur}",
        f"- Uporedivi ukupni iznos (poznato + procene): {pkg.cost.comparable_total:,.2f} {cur}",
        "- Cena je "
        + ("kompletna" if pkg.cost.is_complete else "NEPOTPUNA – nepoznato: " + "; ".join(pkg.cost.unknown_items)),
    ]
    for r in pkg.cost.fx_rates:
        lines.append(
            f"- Kurs: 1 EUR = {r.rate} {r.quote} ({r.source}, {r.as_of}{', REZERVNI' if r.is_fallback else ''})"
        )
    if pkg.caveats:
        lines += ["", "## Napomene", ""] + [f"- {c}" for c in pkg.caveats]
    if itinerary:
        lines += ["", "## Plan putovanja", ""]
        for day in itinerary.days:
            lines += [f"### {day.date:%d.%m.%Y} – {day.title}", ""]
            for it in day.items:
                extra = []
                if it.opening_hours:
                    extra.append(f"radno vreme (OSM): {it.opening_hours}")
                if it.map_url:
                    extra.append(f"[mapa]({it.map_url})")
                if it.source_url:
                    extra.append(f"[izvor]({it.source_url})")
                if not it.verified and it.kind == "sight":
                    extra.append("neprovereno")
                lines.append(
                    f"- {it.start}–{it.end} **{it.title}**"
                    + (f" – {it.description}" if it.description else "")
                    + (f" ({'; '.join(extra)})" if extra else "")
                )
            lines.append(f"- Procena: znamenitosti ~{day.est_sightseeing_eur} EUR, prevoz ~{day.est_transport_eur} EUR")
            lines.append("")
        lines += ["### Transfer", ""] + [
            f"- {t.mode}: {t.description}"
            + (f" (~{t.est_duration_min} min)" if t.est_duration_min else "")
            + (f" ~{t.est_cost_eur} EUR (procena)" if t.est_cost_eur is not None else "")
            + (f" – [ruta]({t.source_url})" if t.source_url else "")
            for t in itinerary.transfers
        ]
        lines += ["", "### Upozorenja", ""] + [f"- {d}" for d in itinerary.disclaimers]
    lines += ["", "---", "TravelScout daje preporuke i preusmerava na provajdere; ne vrši rezervacije ni plaćanja."]
    return "\n".join(lines) + "\n"


def package_to_json(pkg: TripPackage, itinerary: Itinerary | None = None) -> str:
    doc = {
        "package": pkg.model_dump(mode="json"),
        "itinerary": itinerary.model_dump(mode="json") if itinerary else None,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)
