"""User-supplied (manually imported) flight and accommodation offers.

Imported data is validated, stored in SQLite and labelled `manual`. It is treated as
untrusted data: text fields are length-limited and never used as agent instructions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, HttpUrl, field_validator, model_validator

from app.db import Database
from app.domain.airports import KNOWN_AIRPORTS, timezone_for
from app.domain.models import (
    AccommodationOffer,
    BathroomStatus,
    DataOrigin,
    Evidence,
    Fee,
    FlightLeg,
    FlightOption,
    IataCode,
    Money,
    RoomPrivacy,
    SearchConstraints,
    StrictModel,
)
from app.providers.base import DestinationInfo
from app.services.flights import build_round_trips
from app.services.money import FxTable

Text = Field(default=None, max_length=300)


class ManualLeg(StrictModel):
    departure_local: datetime
    arrival_local: datetime | None = None
    flight_number: str | None = Field(default=None, max_length=12)


class ManualFlightInput(StrictModel):
    origin: IataCode
    destination: IataCode
    outbound: ManualLeg
    inbound: ManualLeg
    price_per_seat_outbound: Decimal = Field(gt=0, lt=100000)
    price_per_seat_inbound: Decimal = Field(gt=0, lt=100000)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    fare_type: Literal["regular", "membership"] = "regular"
    baggage_prices: dict[Literal["cabin10", "checked20", "checked32"], Decimal] = Field(default_factory=dict)
    infant_fee_per_leg: Decimal | None = Field(default=None, ge=0)
    included_baggage: str = Field(default="1 mali lični predmet (40×30×20 cm)", max_length=200)
    source_url: HttpUrl | None = None
    notes: str | None = Text

    @model_validator(mode="after")
    def _order(self) -> ManualFlightInput:
        if self.inbound.departure_local <= self.outbound.departure_local:
            raise ValueError("return flight must depart after the outbound flight")
        for leg in (self.outbound, self.inbound):
            if leg.arrival_local and leg.arrival_local < leg.departure_local.replace(hour=0, minute=0):
                raise ValueError("arrival cannot be before the departure date")
        return self


class ManualFeeInput(StrictModel):
    name: str = Field(max_length=80)
    amount: Decimal | None = Field(default=None, ge=0)
    included_in_total: bool = True


class ManualAccommodationInput(StrictModel):
    destination: IataCode
    property_name: str = Field(min_length=2, max_length=150)
    property_type: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    source_url: HttpUrl | None = None
    review_score: Decimal | None = Field(default=None, ge=0, le=10)
    review_count: int | None = Field(default=None, ge=0)
    room_name: str | None = Field(default=None, max_length=150)
    room_privacy: RoomPrivacy = RoomPrivacy.UNKNOWN
    bathroom: BathroomStatus = BathroomStatus.UNKNOWN
    bathroom_evidence: str | None = Text
    max_occupancy: int | None = Field(default=None, ge=1, le=20)
    rooms: int = Field(default=1, ge=1, le=4)
    checkin: date
    checkout: date
    availability_confirmed: bool = False
    total_price: Decimal | None = Field(default=None, gt=0)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    fees: list[ManualFeeInput] = Field(default_factory=list, max_length=10)
    cancellation: str | None = Text
    prepayment: str | None = Text
    distance_km: Decimal | None = Field(default=None, ge=0, le=200)

    @field_validator("source_url")
    @classmethod
    def _https(cls, v: HttpUrl | None) -> HttpUrl | None:
        if v is not None and v.scheme != "https":
            raise ValueError("source_url must be https")
        return v

    @model_validator(mode="after")
    def _dates(self) -> ManualAccommodationInput:
        if self.checkout <= self.checkin:
            raise ValueError("checkout must be after checkin")
        return self


def manual_accommodation_to_offer(mid: str, m: ManualAccommodationInput, imported_at: datetime) -> AccommodationOffer:
    fees = [
        Fee(
            name=f.name,
            amount=Money(amount=f.amount, currency=m.currency) if f.amount is not None else None,
            included_in_total=f.included_in_total,
        )
        for f in m.fees
    ]
    total = m.total_price
    base = None
    if total is not None:
        included = sum((f.amount for f in m.fees if f.amount is not None and f.included_in_total), Decimal(0))
        not_included = sum((f.amount for f in m.fees if f.amount is not None and not f.included_in_total), Decimal(0))
        base = total - included
        total = total + not_included
    url = str(m.source_url) if m.source_url else None
    return AccommodationOffer(
        id=mid,
        destination=m.destination,
        property_name=m.property_name,
        property_type=m.property_type,
        location=m.location,
        source_url=url,
        review_score=m.review_score,
        review_count=m.review_count,
        room_name=m.room_name,
        room_privacy=m.room_privacy,
        bathroom=m.bathroom,
        bathroom_evidence=m.bathroom_evidence or "Uneo korisnik",
        max_occupancy=m.max_occupancy,
        rooms=m.rooms,
        checkin=m.checkin,
        checkout=m.checkout,
        available_for_dates=True if m.availability_confirmed else None,
        base_price=Money(amount=base, currency=m.currency) if base is not None else None,
        fees=fees,
        total_price=Money(amount=total, currency=m.currency) if total is not None else None,
        cancellation=m.cancellation,
        prepayment=m.prepayment,
        distance_km=m.distance_km,
        distance_reference="centra (uneo korisnik)" if m.distance_km is not None else None,
        evidence=Evidence(
            origin=DataOrigin.MANUAL,
            provider="ručni unos",
            source_url=url,
            retrieved_at=imported_at,
            notes=["Podatke je uneo korisnik; nisu automatski provereni."],
        ),
    )


class ManualFlightProvider:
    name = "Ručno uneti letovi"

    def __init__(self, db: Database, fx: FxTable):
        self.db = db
        self.fx = fx

    async def _items(self) -> list[tuple[str, ManualFlightInput, datetime]]:
        rows = await self.db.list_manual_offers("flight")
        out = []
        for r in rows:
            payload = {k: v for k, v in r["payload"].items() if k != "id"}
            out.append((r["id"], ManualFlightInput.model_validate(payload), datetime.fromisoformat(r["created_at"])))
        return out

    async def list_destinations(self, origin: str, direct_only: bool) -> list[DestinationInfo]:
        seen: dict[str, DestinationInfo] = {}
        for _, m, _ in await self._items():
            if m.origin == origin:
                known = KNOWN_AIRPORTS.get(m.destination)
                seen[m.destination] = DestinationInfo(
                    iata=m.destination,
                    name=known.city if known else m.destination,
                    city=known.city if known else None,
                    country=known.country if known else None,
                    country_code=known.country_code if known else None,
                    airport_to_centre_km=known.city_center_km if known else None,
                    timezone=timezone_for(m.destination),
                )
        return list(seen.values())

    async def search_round_trips(
        self, constraints: SearchConstraints, origin: str, destination: DestinationInfo
    ) -> list[FlightOption]:
        options: list[FlightOption] = []
        for mid, m, created in await self._items():
            if m.origin != origin or m.destination != destination.iata:
                continue
            o_tz, d_tz = timezone_for(m.origin), timezone_for(m.destination)
            out = FlightLeg(
                origin=m.origin,
                destination=m.destination,
                departure_local=m.outbound.departure_local,
                departure_tz=o_tz,
                arrival_local=m.outbound.arrival_local,
                arrival_tz=d_tz,
                arrival_is_estimate=m.outbound.arrival_local is None,
                flight_number=m.outbound.flight_number,
                fare_per_seat=Money(amount=m.price_per_seat_outbound, currency=m.currency),
            )
            inb = FlightLeg(
                origin=m.destination,
                destination=m.origin,
                departure_local=m.inbound.departure_local,
                departure_tz=d_tz,
                arrival_local=m.inbound.arrival_local,
                arrival_tz=o_tz,
                arrival_is_estimate=m.inbound.arrival_local is None,
                flight_number=m.inbound.flight_number,
                fare_per_seat=Money(amount=m.price_per_seat_inbound, currency=m.currency),
            )
            url = str(m.source_url) if m.source_url else None

            def evidence(o: FlightLeg, i: FlightLeg, url: str | None = url, created: datetime = created) -> Evidence:
                return Evidence(
                    origin=DataOrigin.MANUAL,
                    provider="ručni unos",
                    source_url=url,
                    retrieved_at=created,
                    notes=["Podatke je uneo korisnik; nisu automatski provereni."],
                )

            def bag_price(key: str, m: ManualFlightInput = m) -> Money | None:
                price = m.baggage_prices.get(key)  # type: ignore[call-overload]
                return Money(amount=price, currency=m.currency) if price is not None else None

            built = build_round_trips(
                constraints,
                [out],
                [inb],
                id_prefix=mid,
                to_eur=lambda x: self.fx.convert(x, "EUR").amount,
                evidence_for=evidence,
                booking_url_for=lambda o, i: url,  # noqa: B023 - consumed immediately
                baggage_price_lookup=bag_price,
                infant_fee=(
                    Money(amount=m.infant_fee_per_leg, currency=m.currency)
                    if m.infant_fee_per_leg is not None
                    else None
                ),
                included_baggage=m.included_baggage,
                limit=1,
            )
            for opt in built:
                options.append(opt.model_copy(update={"fare_type": m.fare_type}))
        return options


class ManualAccommodationProvider:
    name = "Ručno uneti smeštaj"

    def __init__(self, db: Database):
        self.db = db

    async def search(
        self, constraints: SearchConstraints, destination: DestinationInfo, checkin: date, checkout: date
    ) -> list[AccommodationOffer]:
        out = []
        for r in await self.db.list_manual_offers("accommodation"):
            payload = {k: v for k, v in r["payload"].items() if k != "id"}
            m = ManualAccommodationInput.model_validate(payload)
            if m.destination != destination.iata:
                continue
            # Return even date-mismatched imports: eligibility will reject them with a clear reason.
            out.append(
                manual_accommodation_to_offer(r["id"], m, datetime.fromisoformat(r["created_at"]).replace(tzinfo=UTC))
            )
        return out
