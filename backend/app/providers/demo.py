"""Synthetic DEMO dataset.

Deterministic, offline, and obviously fictional (every property name starts with "DEMO").
It exists to exercise the full workflow and every eligibility rule: hostels, low scores,
missing scores, shared/unknown/external bathrooms, small rooms, overnight arrivals and
multiple fare currencies. Nothing here is a real offer and it is never mixed with live data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from app.domain.airports import KNOWN_AIRPORTS, timezone_for
from app.domain.models import (
    AccommodationOffer,
    BathroomStatus,
    DataOrigin,
    Evidence,
    Fee,
    FlightLeg,
    FlightOption,
    Money,
    RoomPrivacy,
    SearchConstraints,
)
from app.providers.base import DestinationInfo
from app.services.flights import build_round_trips
from app.services.money import FxTable, q
from app.services.timeplan import from_utc, to_utc

DEMO_PROVIDER = "TravelScout DEMO skup podataka"
DEMO_NOTE = "DEMO podatak – nije stvarna ponuda."
FARE_CURRENCY_BY_COUNTRY = {"RS": "RSD", "GB": "GBP", "HU": "HUF", "BA": "BAM", "MK": "MKD", "RO": "RON", "BG": "BGN"}


@dataclass(frozen=True)
class DemoRoute:
    dest: str
    out_time: time
    block_min: int
    in_time: time
    base_fare_eur: int
    weekdays: frozenset[int]  # 0 = Monday
    centre: tuple[float, float]
    cost_factor: Decimal


DEMO_ROUTES: dict[str, DemoRoute] = {
    r.dest: r
    for r in [
        DemoRoute(
            "FCO", time(15, 50), 95, time(18, 25), 45, frozenset({0, 2, 4, 6}), (41.8986, 12.4769), Decimal("1.0")
        ),
        DemoRoute(
            "BCN", time(19, 10), 160, time(22, 55), 52, frozenset({1, 3, 5, 6}), (41.3870, 2.1701), Decimal("1.05")
        ),
        DemoRoute(
            "BVA", time(6, 15), 150, time(9, 20), 38, frozenset({0, 1, 3, 4, 5}), (48.8566, 2.3522), Decimal("1.35")
        ),
        DemoRoute(
            "LTN", time(21, 45), 185, time(6, 30), 41, frozenset({0, 2, 3, 4, 6}), (51.5072, -0.1276), Decimal("1.45")
        ),
        DemoRoute("MLA", time(13, 20), 105, time(16, 40), 60, frozenset({1, 4, 6}), (35.8989, 14.5146), Decimal("0.9")),
        DemoRoute("NCE", time(11, 5), 120, time(14, 35), 57, frozenset({0, 3, 5}), (43.7009, 7.2683), Decimal("1.25")),
        DemoRoute("MAD", time(7, 40), 215, time(12, 15), 66, frozenset({2, 5}), (40.4168, -3.7038), Decimal("0.95")),
        DemoRoute(
            "BER",
            time(9, 30),
            110,
            time(20, 10),
            35,
            frozenset({0, 1, 2, 3, 4, 5, 6}),
            (52.5200, 13.4050),
            Decimal("1.1"),
        ),
    ]
}

DEMO_BAGGAGE_EUR = {"cabin10": Decimal("25"), "checked20": Decimal("36"), "checked32": Decimal("52")}
DEMO_INFANT_FEE_EUR = Decimal("30")


@dataclass(frozen=True)
class DemoProperty:
    key: str
    name: str
    ptype: str
    score: Decimal | None
    reviews: int | None
    room: str
    privacy: RoomPrivacy
    bathroom: BathroomStatus
    bathroom_evidence: str
    max_occ: int
    nightly_eur: Decimal
    cleaning_eur: Decimal
    city_tax_pp_night: Decimal
    dist_km: Decimal
    offset: tuple[float, float]
    cancellation: str
    prepayment: str


DEMO_PROPERTIES: list[DemoProperty] = [
    DemoProperty(
        "apt",
        "DEMO Apartman Centar",
        "Apartment",
        Decimal("8.6"),
        412,
        "Apartman sa jednom spavaćom sobom",
        RoomPrivacy.ENTIRE_UNIT,
        BathroomStatus.ENTIRE_UNIT,
        "Ceo apartman, 1 kupatilo (demo)",
        4,
        Decimal("64"),
        Decimal("25"),
        Decimal("3"),
        Decimal("1.8"),
        (0.010, 0.006),
        "Besplatno otkazivanje do 7 dana pre dolaska",
        "Plaćanje u objektu",
    ),
    DemoProperty(
        "hotel",
        "DEMO Hotel Stanica",
        "Hotel",
        Decimal("7.9"),
        1830,
        "Dvokrevetna soba",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.ENSUITE,
        "Ensuite kupatilo u sobi (demo)",
        2,
        Decimal("58"),
        Decimal("0"),
        Decimal("4"),
        Decimal("1.2"),
        (-0.006, 0.009),
        "Bez povraćaja novca",
        "Plaćanje unapred online",
    ),
    DemoProperty(
        "guest",
        "DEMO Pansion Zeleni",
        "Guest house",
        Decimal("8.2"),
        96,
        "Trokrevetna soba",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.PRIVATE_EXTERNAL,
        "Privatno kupatilo van sobe, u hodniku (demo)",
        3,
        Decimal("49"),
        Decimal("0"),
        Decimal("2"),
        Decimal("3.4"),
        (0.024, -0.011),
        "Besplatno otkazivanje do 2 dana pre dolaska",
        "Bez plaćanja unapred",
    ),
    DemoProperty(
        "hostel",
        "DEMO Hostel Central",
        "Hostel",
        Decimal("9.0"),
        2200,
        "Privatna dvokrevetna soba",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.PRIVATE,
        "Privatno kupatilo (demo)",
        2,
        Decimal("33"),
        Decimal("0"),
        Decimal("2"),
        Decimal("0.8"),
        (0.003, 0.002),
        "Besplatno otkazivanje",
        "Bez plaćanja unapred",
    ),
    DemoProperty(
        "lowscore",
        "DEMO Sobe Ekonomik",
        "Guest house",
        Decimal("7.4"),
        530,
        "Dvokrevetna soba",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.PRIVATE,
        "Privatno kupatilo (demo)",
        3,
        Decimal("39"),
        Decimal("0"),
        Decimal("2"),
        Decimal("4.1"),
        (-0.02, -0.015),
        "Bez povraćaja novca",
        "Plaćanje unapred online",
    ),
    DemoProperty(
        "shared",
        "DEMO B&B Zajedničko",
        "Bed and breakfast",
        Decimal("8.8"),
        210,
        "Dvokrevetna soba sa zajedničkim kupatilom",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.SHARED,
        "Zajedničko kupatilo (demo)",
        3,
        Decimal("37"),
        Decimal("0"),
        Decimal("2"),
        Decimal("2.2"),
        (0.015, 0.02),
        "Besplatno otkazivanje",
        "Bez plaćanja unapred",
    ),
    DemoProperty(
        "noscore",
        "DEMO Nova Rezidencija",
        "Apartment",
        None,
        None,
        "Studio apartman",
        RoomPrivacy.ENTIRE_UNIT,
        BathroomStatus.ENTIRE_UNIT,
        "Ceo studio (demo)",
        3,
        Decimal("42"),
        Decimal("20"),
        Decimal("2"),
        Decimal("2.7"),
        (-0.012, 0.018),
        "Besplatno otkazivanje",
        "Bez plaćanja unapred",
    ),
    DemoProperty(
        "unknownbath",
        "DEMO Vila Bez Detalja",
        "Guest house",
        Decimal("8.4"),
        75,
        "Porodična soba",
        RoomPrivacy.PRIVATE_ROOM,
        BathroomStatus.UNKNOWN,
        "Kupatilo nije navedeno za sobu (demo)",
        4,
        Decimal("44"),
        Decimal("0"),
        Decimal("2"),
        Decimal("2.9"),
        (0.018, -0.02),
        "Bez povraćaja novca",
        "Plaćanje unapred online",
    ),
]


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def demo_destinations() -> dict[str, DestinationInfo]:
    out = {}
    for code in DEMO_ROUTES:
        a = KNOWN_AIRPORTS[code]
        out[code] = DestinationInfo(
            iata=code,
            name=a.city,
            city=a.city,
            country=a.country,
            country_code=a.country_code,
            lat=a.lat,
            lon=a.lon,
            airport_to_centre_km=a.city_center_km,
            timezone=timezone_for(code),
        )
    return out


class DemoFlightProvider:
    name = "DEMO letovi (Wizz Air format)"

    def __init__(self, fx: FxTable):
        self.fx = fx
        self.retrieved_at = datetime.now(UTC)

    async def list_destinations(self, origin: str, direct_only: bool) -> list[DestinationInfo]:
        if origin not in KNOWN_AIRPORTS:
            return []
        return list(demo_destinations().values())

    def _fare(self, origin: str, dest: str, day: date, base_eur: int, outbound: bool) -> Money:
        factor = Decimal(60 + _h(origin, dest, day, outbound) % 110) / 100  # 0.60 .. 1.69
        eur = Money(amount=q(Decimal(base_eur) * factor), currency="EUR")
        # Like Wizz Air, fares are quoted in the departure airport's currency.
        cur = FARE_CURRENCY_BY_COUNTRY.get(KNOWN_AIRPORTS[origin].country_code, "EUR")
        return self.fx.convert(eur, cur)

    def _leg(self, origin: str, dest: str, day: date, dep: time, block: int, base: int, outbound: bool) -> FlightLeg:
        o_tz, d_tz = timezone_for(origin), timezone_for(dest)
        dep_local = datetime.combine(day, dep)
        arr = from_utc(to_utc(dep_local, o_tz) + timedelta(minutes=block), d_tz) if o_tz and d_tz else None
        num = f"W6 9{_h(origin, dest) % 900 + 100}"
        return FlightLeg(
            origin=origin,
            destination=dest,
            departure_local=dep_local,
            departure_tz=o_tz,
            arrival_local=arr,
            arrival_tz=d_tz,
            arrival_is_estimate=False,
            flight_number=num,
            fare_per_seat=self._fare(origin, dest, day, base, outbound),
        )

    async def search_round_trips(
        self, constraints: SearchConstraints, origin: str, destination: DestinationInfo
    ) -> list[FlightOption]:
        route = DEMO_ROUTES.get(destination.iata)
        if route is None:
            return []
        d0, d1 = constraints.outbound_dates
        last_return = (
            constraints.return_date if constraints.return_date else d1 + timedelta(days=constraints.max_nights + 1)
        )
        outbound, inbound = [], []
        day = d0
        while day <= last_return:
            if day.weekday() in route.weekdays:
                if day <= d1:
                    outbound.append(
                        self._leg(origin, route.dest, day, route.out_time, route.block_min, route.base_fare_eur, True)
                    )
                inbound.append(
                    self._leg(route.dest, origin, day, route.in_time, route.block_min, route.base_fare_eur, False)
                )
            day += timedelta(days=1)
        p = constraints.party

        def evidence(o: FlightLeg, i: FlightLeg) -> Evidence:
            return Evidence(
                origin=DataOrigin.DEMO,
                provider=DEMO_PROVIDER,
                retrieved_at=self.retrieved_at,
                source_url=None,
                notes=[DEMO_NOTE],
            )

        return build_round_trips(
            constraints,
            outbound,
            inbound,
            id_prefix=f"demo-{destination.iata.lower()}",
            to_eur=lambda m: self.fx.convert(m, "EUR").amount,
            evidence_for=evidence,
            booking_url_for=lambda o, i: None,
            baggage_price_lookup=lambda k: Money(amount=DEMO_BAGGAGE_EUR[k], currency="EUR"),
            infant_fee=Money(amount=DEMO_INFANT_FEE_EUR, currency="EUR") if p.infants else None,
            child_note="DEMO: deca plaćaju istu cenu kao odrasli",
            fare_is_confirmed_quote=False,
        )


class DemoAccommodationProvider:
    name = "DEMO smeštaj (Booking.com format)"

    async def search(
        self, constraints: SearchConstraints, destination: DestinationInfo, checkin: date, checkout: date
    ) -> list[AccommodationOffer]:
        route = DEMO_ROUTES.get(destination.iata)
        if route is None:
            return []
        nights = (checkout - checkin).days
        rooms = constraints.rooms
        adults = constraints.party.adults
        offers = []
        for prop in DEMO_PROPERTIES:
            jitter = Decimal(75 + _h(destination.iata, prop.key, checkin) % 55) / 100  # 0.75 .. 1.29
            nightly = q(prop.nightly_eur * route.cost_factor * jitter)
            base = q(nightly * nights * rooms)
            fees: list[Fee] = []
            if prop.cleaning_eur:
                fees.append(
                    Fee(
                        name="Naknada za čišćenje",
                        amount=Money(amount=prop.cleaning_eur * rooms, currency="EUR"),
                        included_in_total=True,
                    )
                )
            fees.append(
                Fee(
                    name="Boravišna taksa",
                    amount=Money(amount=q(prop.city_tax_pp_night * adults * nights), currency="EUR"),
                    included_in_total=True,
                )
            )
            total = base + sum((f.amount.amount for f in fees if f.amount), Decimal(0))
            lat, lon = route.centre[0] + prop.offset[0], route.centre[1] + prop.offset[1]
            offers.append(
                AccommodationOffer(
                    id=f"demo-{destination.iata.lower()}-{prop.key}-{checkin:%m%d}",
                    destination=destination.iata,
                    property_name=f"{prop.name} {destination.name}",
                    property_type=prop.ptype,
                    location=f"{destination.name} (demo lokacija)",
                    latitude=round(lat, 5),
                    longitude=round(lon, 5),
                    source_url=None,
                    review_score=prop.score,
                    review_count=prop.reviews,
                    room_name=prop.room,
                    room_privacy=prop.privacy,
                    bathroom=prop.bathroom,
                    bathroom_evidence=prop.bathroom_evidence,
                    max_occupancy=prop.max_occ,
                    rooms=rooms,
                    checkin=checkin,
                    checkout=checkout,
                    available_for_dates=True,
                    base_price=Money(amount=base, currency="EUR"),
                    fees=fees,
                    total_price=Money(amount=total, currency="EUR"),
                    cancellation=prop.cancellation,
                    prepayment=prop.prepayment,
                    distance_km=prop.dist_km,
                    distance_reference="centra grada (demo)",
                    evidence=Evidence(
                        origin=DataOrigin.DEMO,
                        provider=DEMO_PROVIDER,
                        retrieved_at=datetime.now(UTC),
                        notes=[DEMO_NOTE],
                    ),
                )
            )
        return offers
