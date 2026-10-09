"""Provider interfaces. Concrete providers: live (Wizz/Booking/OSM), manual import, demo."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from app.domain.models import AccommodationOffer, FlightOption, PointOfInterest, SearchConstraints


class DestinationInfo(BaseModel):
    iata: str
    name: str  # display name, e.g. "Rome Fiumicino"
    city: str | None = None  # city to search accommodation in, e.g. "Rome"
    country: str | None = None
    country_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    airport_to_centre_km: float | None = None
    timezone: str | None = None


class ProviderError(RuntimeError):
    """A provider could not deliver data. Message must be safe to show to users."""


class ProviderBlockedError(ProviderError):
    """The site actively blocked automated access (bot protection, CAPTCHA). Never bypassed."""


class FlightProvider(Protocol):
    name: str

    async def list_destinations(self, origin: str, direct_only: bool) -> list[DestinationInfo]: ...

    async def search_round_trips(
        self, constraints: SearchConstraints, origin: str, destination: DestinationInfo
    ) -> list[FlightOption]: ...


class AccommodationProvider(Protocol):
    name: str

    async def search(
        self, constraints: SearchConstraints, destination: DestinationInfo, checkin: date, checkout: date
    ) -> list[AccommodationOffer]: ...


class PoiProvider(Protocol):
    name: str

    async def geocode(self, query: str) -> tuple[float, float] | None: ...

    async def attractions(self, lat: float, lon: float, radius_m: int, limit: int) -> list[PointOfInterest]: ...
