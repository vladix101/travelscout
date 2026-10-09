"""Static airport reference data.

Live route data comes from the Wizz Air route map; this module only provides:
* selectable departure airports near Serbia,
* an IANA time zone for an airport (country default + explicit overrides),
* a small fallback catalogue used by the demo dataset and when the route map is offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Airport:
    iata: str
    city: str
    country: str
    country_code: str
    lat: float
    lon: float
    city_center_km: float | None = None  # rough airport -> city centre distance (estimate)


ORIGIN_AIRPORTS: list[Airport] = [
    Airport("BEG", "Beograd", "Serbia", "RS", 44.8184, 20.3091, 18),
    Airport("INI", "Niš", "Serbia", "RS", 43.3373, 21.8537, 5),
    Airport("TZL", "Tuzla", "Bosnia and Herzegovina", "BA", 44.4587, 18.7248, 15),
    Airport("SKP", "Skopje", "North Macedonia", "MK", 41.9616, 21.6214, 21),
    Airport("TSR", "Timișoara", "Romania", "RO", 45.8099, 21.3379, 12),
    Airport("BUD", "Budimpešta", "Hungary", "HU", 47.4369, 19.2556, 16),
    Airport("SOF", "Sofija", "Bulgaria", "BG", 42.6952, 23.4062, 10),
    Airport("TGD", "Podgorica", "Montenegro", "ME", 42.3594, 19.2519, 11),
]

# Fallback catalogue (also used by the demo dataset). Coordinates are public airport data.
KNOWN_AIRPORTS: dict[str, Airport] = {
    a.iata: a
    for a in [
        *ORIGIN_AIRPORTS,
        Airport("FCO", "Rome", "Italy", "IT", 41.8003, 12.2389, 30),
        Airport("CIA", "Rome", "Italy", "IT", 41.7994, 12.5949, 15),
        Airport("BCN", "Barcelona", "Spain", "ES", 41.2971, 2.0785, 15),
        Airport("BVA", "Paris", "France", "FR", 49.4544, 2.1128, 85),
        Airport("LTN", "London", "United Kingdom", "GB", 51.8747, -0.3683, 55),
        Airport("MLA", "Malta", "Malta", "MT", 35.8575, 14.4775, 8),
        Airport("NCE", "Nice", "France", "FR", 43.6584, 7.2159, 7),
        Airport("MAD", "Madrid", "Spain", "ES", 40.4983, -3.5676, 13),
        Airport("BER", "Berlin", "Germany", "DE", 52.3667, 13.5033, 27),
        Airport("BGY", "Milan", "Italy", "IT", 45.6739, 9.7042, 50),
        Airport("LCA", "Larnaca", "Cyprus", "CY", 34.8751, 33.6249, 5),
        Airport("ALC", "Alicante", "Spain", "ES", 38.2822, -0.5582, 10),
        Airport("DTM", "Dortmund", "Germany", "DE", 51.5183, 7.6122, 13),
        Airport("HAM", "Hamburg", "Germany", "DE", 53.6304, 9.9882, 9),
        Airport("PMO", "Palermo", "Italy", "IT", 38.1759, 13.0910, 32),
        Airport("PSA", "Pisa", "Italy", "IT", 43.6839, 10.3927, 2),
        Airport("EIN", "Eindhoven", "Netherlands", "NL", 51.4501, 5.3745, 8),
        Airport("GOT", "Gothenburg", "Sweden", "SE", 57.6628, 12.2798, 20),
        Airport("MMX", "Malmö", "Sweden", "SE", 55.5363, 13.3762, 28),
        Airport("NYO", "Stockholm", "Sweden", "SE", 58.7886, 16.9122, 100),
        Airport("MLH", "Basel", "Switzerland", "CH", 47.5896, 7.5299, 6),
        Airport("FMM", "Memmingen", "Germany", "DE", 47.9888, 10.2395, 4),
    ]
}

COUNTRY_TZ: dict[str, str] = {
    "AL": "Europe/Tirane",
    "AT": "Europe/Vienna",
    "BA": "Europe/Sarajevo",
    "BE": "Europe/Brussels",
    "BG": "Europe/Sofia",
    "CH": "Europe/Zurich",
    "CY": "Asia/Nicosia",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK": "Europe/Copenhagen",
    "EE": "Europe/Tallinn",
    "ES": "Europe/Madrid",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "GE": "Asia/Tbilisi",
    "GR": "Europe/Athens",
    "HR": "Europe/Zagreb",
    "HU": "Europe/Budapest",
    "IE": "Europe/Dublin",
    "IL": "Asia/Jerusalem",
    "IS": "Atlantic/Reykjavik",
    "IT": "Europe/Rome",
    "JO": "Asia/Amman",
    "KZ": "Asia/Almaty",
    "LT": "Europe/Vilnius",
    "LU": "Europe/Luxembourg",
    "LV": "Europe/Riga",
    "MA": "Africa/Casablanca",
    "MD": "Europe/Chisinau",
    "ME": "Europe/Podgorica",
    "MK": "Europe/Skopje",
    "MT": "Europe/Malta",
    "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo",
    "PL": "Europe/Warsaw",
    "PT": "Europe/Lisbon",
    "RO": "Europe/Bucharest",
    "RS": "Europe/Belgrade",
    "SA": "Asia/Riyadh",
    "SE": "Europe/Stockholm",
    "SI": "Europe/Ljubljana",
    "SK": "Europe/Bratislava",
    "TR": "Europe/Istanbul",
    "UA": "Europe/Kyiv",
    "AE": "Asia/Dubai",
    "EG": "Africa/Cairo",
    "AM": "Asia/Yerevan",
    "AZ": "Asia/Baku",
    "UZ": "Asia/Tashkent",
    "MV": "Indian/Maldives",
    "XK": "Europe/Belgrade",
}

# Airports whose zone differs from their country's default.
AIRPORT_TZ_OVERRIDES: dict[str, str] = {
    **dict.fromkeys(["LPA", "TFS", "TFN", "ACE", "FUE", "SPC"], "Atlantic/Canary"),
    "FNC": "Atlantic/Madeira",
    "PDL": "Atlantic/Azores",
    "TER": "Atlantic/Azores",
    **dict.fromkeys(["NQZ", "TSE", "GUW", "SCO", "AKX"], "Asia/Almaty"),
}


def timezone_for(iata: str, country_code: str | None = None) -> str | None:
    """Best-effort IANA zone for an airport; None when it cannot be established."""
    if iata in AIRPORT_TZ_OVERRIDES:
        return AIRPORT_TZ_OVERRIDES[iata]
    cc = country_code or (KNOWN_AIRPORTS[iata].country_code if iata in KNOWN_AIRPORTS else None)
    tz = COUNTRY_TZ.get(cc or "")
    if tz:
        ZoneInfo(tz)  # validate it exists in tzdata
    return tz
