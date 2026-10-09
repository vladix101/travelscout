"""Typed domain models shared by providers, agents, the LangGraph workflow and the API.

Money is always `Decimal` (never float). Every externally sourced record carries an
`Evidence` block that separates *where the data came from / how fresh it is* (`origin`)
from *whether it satisfies our rules* (eligibility, handled elsewhere).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IataCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------------------
# Evidence / provenance
# ---------------------------------------------------------------------------------------


class DataOrigin(StrEnum):
    LIVE = "live"  # fetched from the provider during this search
    CACHED = "cached"  # fetched earlier from the provider, served from local cache
    MANUAL = "manual"  # imported by the user
    DEMO = "demo"  # synthetic demo dataset, never real offers
    UNVERIFIED = "unverified"  # general knowledge / estimate / discovery lead


class Evidence(StrictModel):
    origin: DataOrigin
    provider: str
    source_url: str | None = None
    retrieved_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class Money(StrictModel):
    amount: Decimal
    currency: CurrencyCode

    @field_validator("amount")
    @classmethod
    def _finite(cls, v: Decimal) -> Decimal:
        if not v.is_finite():
            raise ValueError("amount must be finite")
        return v


# ---------------------------------------------------------------------------------------
# Search constraints (Travel Coordinator output)
# ---------------------------------------------------------------------------------------


class Party(StrictModel):
    adults: int = Field(default=2, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    child_ages: list[int] = Field(default_factory=list)
    infants: int = Field(default=0, ge=0, le=4)

    @model_validator(mode="after")
    def _check(self) -> Party:
        if len(self.child_ages) != self.children:
            raise ValueError("child_ages must contain one age per child")
        if any(a < 2 or a > 17 for a in self.child_ages):
            raise ValueError("child ages must be between 2 and 17 (under 2 counts as infant)")
        if self.infants > self.adults:
            raise ValueError("each infant must travel on an adult's lap (infants <= adults)")
        return self

    @property
    def seated_passengers(self) -> int:
        return self.adults + self.children

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class BaggageRequirement(StrictModel):
    cabin_trolley_10kg: int = Field(default=0, ge=0, le=9, description="Number of 10 kg trolley bags")
    checked_bags_20kg: int = Field(default=0, ge=0, le=9)
    checked_bags_32kg: int = Field(default=0, ge=0, le=9)

    @property
    def any_extra(self) -> bool:
        return bool(self.cabin_trolley_10kg or self.checked_bags_20kg or self.checked_bags_32kg)


class DateMode(StrEnum):
    EXACT = "exact"
    FLEXIBLE = "flexible"


class BudgetScope(StrEnum):
    PER_PERSON = "per_person"
    GROUP = "group"


class Pace(StrEnum):
    RELAXED = "relaxed"
    MODERATE = "moderate"
    INTENSE = "intense"


class RankingPreferences(StrictModel):
    quality_weight: float = Field(default=0.5, ge=0, le=1)
    location_weight: float = Field(default=0.5, ge=0, le=1)
    time_weight: float = Field(default=0.5, ge=0, le=1)


MIN_REVIEW_SCORE = Decimal("7.5")


class SearchConstraints(StrictModel):
    origins: list[IataCode] = Field(default_factory=lambda: ["BEG"], min_length=1, max_length=6)
    date_mode: DateMode = DateMode.FLEXIBLE
    departure_date: date | None = None
    return_date: date | None = None
    window_start: date | None = None
    window_end: date | None = None
    min_nights: int = Field(default=2, ge=1, le=21)
    max_nights: int = Field(default=5, ge=1, le=21)
    destinations: list[IataCode] = Field(default_factory=list, max_length=20)
    anywhere: bool = True
    party: Party = Field(default_factory=Party)
    budget_amount: Decimal | None = Field(default=None, gt=0)
    budget_scope: BudgetScope = BudgetScope.PER_PERSON
    budget_currency: CurrencyCode = "EUR"
    baggage: BaggageRequirement = Field(default_factory=BaggageRequirement)
    direct_only: bool = True
    interests: list[str] = Field(default_factory=list, max_length=12)
    pace: Pace = Pace.MODERATE
    min_review_score: Decimal = Field(default=MIN_REVIEW_SCORE, le=Decimal("10"))
    max_distance_km: Decimal | None = Field(default=None, gt=0)
    rooms: int = Field(default=1, ge=1, le=4)
    display_currency: Literal["EUR", "RSD"] = "EUR"
    ranking: RankingPreferences = Field(default_factory=RankingPreferences)

    @field_validator("interests")
    @classmethod
    def _clean_interests(cls, v: list[str]) -> list[str]:
        return [i[:40] for i in (s.strip() for s in v) if i]

    @field_validator("min_review_score")
    @classmethod
    def _score_floor(cls, v: Decimal) -> Decimal:
        # The 7.5 floor is a product rule: callers may raise it but never lower it.
        return max(v, MIN_REVIEW_SCORE)

    @model_validator(mode="after")
    def _check_dates(self) -> SearchConstraints:
        if self.min_nights > self.max_nights:
            raise ValueError("min_nights must be <= max_nights")
        if self.date_mode is DateMode.EXACT:
            if not (self.departure_date and self.return_date):
                raise ValueError("exact mode requires departure_date and return_date")
            if self.return_date <= self.departure_date:
                raise ValueError("return_date must be after departure_date")
        else:
            if not (self.window_start and self.window_end):
                raise ValueError("flexible mode requires window_start and window_end")
            if self.window_end < self.window_start:
                raise ValueError("window_end must be on/after window_start")
            if (self.window_end - self.window_start).days > 62:
                raise ValueError("flexible departure window is limited to 62 days")
        if not self.anywhere and not self.destinations:
            raise ValueError("choose at least one destination or 'anywhere'")
        if self.party.seated_passengers + self.party.infants > self.rooms * 6:
            raise ValueError("party is too large for the selected number of rooms")
        return self

    @property
    def outbound_dates(self) -> tuple[date, date]:
        if self.date_mode is DateMode.EXACT:
            assert self.departure_date
            return self.departure_date, self.departure_date
        assert self.window_start and self.window_end
        return self.window_start, self.window_end


class InterpretationNote(StrictModel):
    field: str
    message: str
    severity: Literal["info", "warning"] = "info"


class InterpretedRequest(StrictModel):
    constraints: SearchConstraints
    notes: list[InterpretationNote] = Field(default_factory=list)
    used_llm: bool = False


# ---------------------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------------------


class FareType(StrEnum):
    REGULAR = "regular"
    MEMBERSHIP = "membership"  # e.g. Wizz Discount Club; never assumed


class FlightLeg(StrictModel):
    origin: IataCode
    destination: IataCode
    departure_local: datetime  # naive local wall time at origin
    departure_tz: str | None
    arrival_local: datetime | None = None  # naive local wall time at destination
    arrival_tz: str | None = None
    arrival_is_estimate: bool = True
    flight_number: str | None = None
    fare_per_seat: Money
    direct: bool = True


class PassengerPrice(StrictModel):
    passenger_type: Literal["adult", "child", "infant"]
    count: int
    fare_per_person: Money | None  # None = unknown
    note: str | None = None


class BaggageQuote(StrictModel):
    item: str
    quantity: int
    unit_price: Money | None  # None = unknown price
    note: str | None = None


class FlightOption(StrictModel):
    id: str
    airline: Literal["Wizz Air"] = "Wizz Air"
    outbound: FlightLeg
    inbound: FlightLeg
    passengers: list[PassengerPrice]
    total_fare: Money  # party total for seated passengers, normalized to EUR for comparison
    total_fare_by_leg: list[Money]
    included_baggage: str
    baggage_quotes: list[BaggageQuote] = Field(default_factory=list)
    fare_type: FareType = FareType.REGULAR
    fare_is_confirmed_quote: bool = False
    evidence: Evidence
    booking_url: str | None = None


# ---------------------------------------------------------------------------------------
# Accommodation
# ---------------------------------------------------------------------------------------


class BathroomStatus(StrEnum):
    ENSUITE = "ensuite"  # explicitly en-suite / inside the room
    PRIVATE = "private"  # private, location (en-suite vs external) not stated
    PRIVATE_EXTERNAL = "private_external"  # private but outside the room
    ENTIRE_UNIT = "entire_unit"  # entire apartment/house with its own bathroom(s)
    SHARED = "shared"
    UNKNOWN = "unknown"


PRIVATE_BATHROOM_STATUSES = {
    BathroomStatus.ENSUITE,
    BathroomStatus.PRIVATE,
    BathroomStatus.PRIVATE_EXTERNAL,
    BathroomStatus.ENTIRE_UNIT,
}


class RoomPrivacy(StrEnum):
    PRIVATE_ROOM = "private_room"
    ENTIRE_UNIT = "entire_unit"
    SHARED_SPACE = "shared_space"  # dorm / shared room
    UNKNOWN = "unknown"


class Fee(StrictModel):
    name: str
    amount: Money | None  # None = mandatory but amount unknown
    included_in_total: bool
    mandatory: bool = True


class AccommodationOffer(StrictModel):
    id: str
    destination: IataCode
    property_name: str
    property_type: str | None  # e.g. Hotel, Apartment, Guesthouse, Hostel
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_url: str | None = None
    review_score: Decimal | None = None
    review_count: int | None = None
    room_name: str | None = None
    room_privacy: RoomPrivacy = RoomPrivacy.UNKNOWN
    bathroom: BathroomStatus = BathroomStatus.UNKNOWN
    bathroom_evidence: str | None = None
    max_occupancy: int | None = None
    rooms: int = 1
    checkin: date
    checkout: date
    available_for_dates: bool | None = None
    base_price: Money | None = None  # room price before excluded fees
    fees: list[Fee] = Field(default_factory=list)
    total_price: Money | None = None  # whole party, whole stay, incl. known mandatory fees
    cancellation: str | None = None
    prepayment: str | None = None
    distance_km: Decimal | None = None
    distance_reference: str | None = None
    evidence: Evidence

    @property
    def nights(self) -> int:
        return (self.checkout - self.checkin).days


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    NEEDS_VERIFICATION = "needs_verification"
    INELIGIBLE = "ineligible"


class EligibilityResult(StrictModel):
    status: EligibilityStatus
    failed: list[str] = Field(default_factory=list)  # hard rule failures (codes)
    unverified: list[str] = Field(default_factory=list)  # missing evidence (codes)
    messages: list[str] = Field(default_factory=list)


class AssessedOffer(StrictModel):
    offer: AccommodationOffer
    eligibility: EligibilityResult


# ---------------------------------------------------------------------------------------
# Costs, packages, ranking
# ---------------------------------------------------------------------------------------


class FxRate(StrictModel):
    base: CurrencyCode
    quote: CurrencyCode
    rate: Decimal  # 1 base = rate quote
    source: str
    as_of: date
    is_fallback: bool = False


class CostLine(StrictModel):
    label: str
    amount: Money | None
    category: Literal["mandatory", "extra", "estimate", "unknown"]
    note: str | None = None


class CostBreakdown(StrictModel):
    currency: CurrencyCode
    lines: list[CostLine]
    known_mandatory: Decimal
    known_extras: Decimal
    estimated: Decimal
    unknown_items: list[str]
    known_total: Decimal  # mandatory + known extras
    comparable_total: Decimal  # known_total + estimates (used for ranking only)
    per_person_known: Decimal
    is_complete: bool  # True only when there are no unknown items
    fx_rates: list[FxRate] = Field(default_factory=list)


class UsableTime(StrictModel):
    nights: int
    full_days: int
    usable_hours: float
    arrival_note: str | None = None
    departure_note: str | None = None
    overnight_arrival: bool = False


class TripPackage(StrictModel):
    id: str
    destination: IataCode
    destination_name: str
    country: str | None = None
    flight: FlightOption
    accommodation: AssessedOffer
    cost: CostBreakdown
    usable_time: UsableTime
    rank: int = 0
    rank_score: Decimal = Decimal("0")
    highlights: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    data_origins: list[DataOrigin] = Field(default_factory=list)


class Diagnostic(StrictModel):
    stage: str
    code: str
    message: str
    count: int | None = None


class ProviderRunStatus(StrictModel):
    provider: str
    status: Literal["ok", "partial", "failed", "skipped", "disabled"]
    message: str | None = None
    origin: DataOrigin | None = None
    checked_at: datetime | None = None


class DestinationShortlist(StrictModel):
    destination: IataCode
    destination_name: str
    flight_ids: list[str]


class SearchResult(StrictModel):
    constraints: SearchConstraints
    mode: Literal["demo", "live"]
    packages: list[TripPackage]
    flight_shortlist: list[FlightOption] = Field(default_factory=list)
    destination_cities: dict[str, str] = Field(default_factory=dict)
    needs_verification: list[AssessedOffer]
    rejected_counts: dict[str, int]
    diagnostics: list[Diagnostic]
    suggestions: list[str]
    provider_status: list[ProviderRunStatus]
    agent_notes: list[str]
    used_llm: bool
    fx_status: str = ""
    partial: bool = False
    generated_at: datetime


# ---------------------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------------------


class PointOfInterest(StrictModel):
    id: str
    name: str
    category: str
    latitude: float
    longitude: float
    opening_hours: str | None = None  # raw OSM opening_hours tag (community data)
    fee: Literal["yes", "no"] | None = None
    website: str | None = None
    wikipedia: str | None = None
    map_url: str
    source: str  # e.g. "OpenStreetMap"
    origin: DataOrigin


class ItineraryItem(StrictModel):
    start: str  # "HH:MM" local
    end: str
    title: str
    kind: Literal["sight", "meal", "transfer", "break", "free_time", "checkin", "checkout"]
    poi_id: str | None = None
    area: str | None = None
    description: str | None = None
    paid: bool | None = None
    map_url: str | None = None
    source_url: str | None = None
    opening_hours: str | None = None
    verified: bool = False  # True only when facts came from a documented data source


class ItineraryDay(StrictModel):
    date: date
    title: str
    area: str | None = None
    items: list[ItineraryItem]
    est_sightseeing_eur: Decimal = Decimal("0")
    est_transport_eur: Decimal = Decimal("0")


class TransferOption(StrictModel):
    mode: str
    description: str
    est_duration_min: int | None = None
    est_cost_eur: Decimal | None = None
    verified: bool = False
    source_url: str | None = None


class Itinerary(StrictModel):
    package_id: str
    destination_name: str
    days: list[ItineraryDay]
    transfers: list[TransferOption]
    pois: list[PointOfInterest]
    sources: list[str]
    disclaimers: list[str]
    used_llm: bool
    generated_at: datetime
