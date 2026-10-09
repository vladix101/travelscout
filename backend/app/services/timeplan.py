"""Deterministic date / time-zone calculations for trips."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.models import FlightLeg, UsableTime

DAY_START = time(8, 0)
DAY_END = time(21, 0)
EARLY_ARRIVAL_CUTOFF = time(6, 0)  # arriving before this needs the room from the previous night
ARRIVAL_BUFFER = timedelta(minutes=45)  # deplaning, passport control
DEPARTURE_BUFFER = timedelta(hours=2, minutes=30)  # be at the airport before departure


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_block_minutes(distance_km: float) -> int:
    """Rough gate-to-gate time for a narrow-body jet (taxi/climb overhead + cruise)."""
    return int(round((35 + distance_km / 780 * 60) / 5.0) * 5)


def to_utc(local: datetime, tz: str) -> datetime:
    return local.replace(tzinfo=ZoneInfo(tz)).astimezone(ZoneInfo("UTC"))


def from_utc(utc: datetime, tz: str) -> datetime:
    return utc.astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def estimate_arrival_local(
    departure_local: datetime, departure_tz: str | None, arrival_tz: str | None, block_minutes: int
) -> datetime | None:
    """Arrival wall-clock time at destination, or None if a time zone is unknown."""
    if not departure_tz or not arrival_tz:
        return None
    arr_utc = to_utc(departure_local, departure_tz) + timedelta(minutes=block_minutes)
    return from_utc(arr_utc, arrival_tz)


def stay_dates(outbound: FlightLeg, inbound: FlightLeg) -> tuple[date, date, bool]:
    """Return (checkin, checkout, overnight_arrival).

    If the outbound lands between midnight and 06:00 local time the room is needed from the
    previous calendar night, otherwise the traveller would have nowhere to go on arrival.
    """
    arrival = outbound.arrival_local or outbound.departure_local
    overnight = arrival.date() > outbound.departure_local.date() or arrival.time() < EARLY_ARRIVAL_CUTOFF
    checkin = arrival.date() - timedelta(days=1) if arrival.time() < EARLY_ARRIVAL_CUTOFF else arrival.date()
    checkout = inbound.departure_local.date()
    if checkout <= checkin:
        raise ValueError("return flight departs before the stay starts")
    return checkin, checkout, overnight


def usable_time(outbound: FlightLeg, inbound: FlightLeg, transfer_minutes: int = 60) -> UsableTime:
    """Sightseeing hours available between arrival (+transfer) and departure (-transfer, -buffer)."""
    checkin, checkout, overnight = stay_dates(outbound, inbound)
    arrival = outbound.arrival_local or outbound.departure_local
    free_from = arrival + ARRIVAL_BUFFER + timedelta(minutes=transfer_minutes)
    free_until = inbound.departure_local - DEPARTURE_BUFFER - timedelta(minutes=transfer_minutes)

    hours = 0.0
    full_days = 0
    day = free_from.date()
    while day <= free_until.date():
        window_start = datetime.combine(day, DAY_START)
        window_end = datetime.combine(day, DAY_END)
        start = max(window_start, free_from)
        end = min(window_end, free_until)
        if end > start:
            h = (end - start).total_seconds() / 3600
            hours += h
            if start == window_start and end == window_end:
                full_days += 1
        day += timedelta(days=1)

    arrival_note = None
    if outbound.arrival_local is None:
        arrival_note = "Vreme sletanja nije poznato; računato kao da je sletanje u vreme poletanja."
    elif overnight:
        arrival_note = "Noćni dolazak: smeštaj je potreban od prethodne noći."
    elif arrival.time() >= time(18, 0):
        arrival_note = "Kasni dolazak: prvi dan je praktično samo za smeštaj."
    departure_note = None
    if inbound.departure_local.time() < time(10, 0):
        departure_note = "Rani povratni let: poslednji dan nije iskoristiv za obilazak."

    return UsableTime(
        nights=(checkout - checkin).days,
        full_days=full_days,
        usable_hours=round(hours, 1),
        arrival_note=arrival_note,
        departure_note=departure_note,
        overnight_arrival=overnight,
    )
