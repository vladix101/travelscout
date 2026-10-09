"""Exchange rates.

* ECB reference rates via Frankfurter (https://frankfurter.dev, documented, no key).
* RSD is not published by the ECB; we use the National Bank of Serbia official middle rate
  as exposed by kurs.resenje.org (documented public API over NBS data).
* If either source fails, clearly flagged static fallback rates are used for that currency.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app.db import Database
from app.domain.models import FxRate
from app.providers.http import make_client, request_with_retries
from app.services.money import FxTable

log = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=EUR"
NBS_URL = "https://kurs.resenje.org/api/v1/currencies/eur/rates/today"
CACHE_KEY = "fx:eur:v1"


async def load_fx_table(db: Database | None, *, live: bool, ttl_seconds: int = 6 * 3600) -> tuple[FxTable, str]:
    """Return (table, status_message). Never raises: falls back to static rates."""
    table = FxTable.fallback()
    if not live:
        return table, "Isključeni živi kursevi – koriste se rezervni kursevi."

    if db is not None:
        cached = await db.cache_get(CACHE_KEY)
        if cached:
            for r in cached[0]:
                table.add(FxRate.model_validate(r))
            return table, "Kursevi iz keša (ECB + NBS)."

    fetched: list[FxRate] = []
    messages: list[str] = []
    async with make_client(timeout=15) as client:
        try:
            resp = await request_with_retries(client, "GET", FRANKFURTER_URL)
            data = resp.json()
            as_of = date.fromisoformat(data["date"])
            for cur, val in data["rates"].items():
                fetched.append(
                    FxRate(base="EUR", quote=cur, rate=Decimal(str(val)), source="ECB via frankfurter.dev", as_of=as_of)
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("ECB rates unavailable: %s", type(exc).__name__)
            messages.append("ECB kursevi nedostupni")
        try:
            resp = await request_with_retries(client, "GET", NBS_URL)
            data = resp.json()
            fetched.append(
                FxRate(
                    base="EUR",
                    quote="RSD",
                    rate=Decimal(str(data["exchange_middle"])),
                    source="NBS srednji kurs via kurs.resenje.org",
                    as_of=date.fromisoformat(data["date"]),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("NBS rate unavailable: %s", type(exc).__name__)
            messages.append("NBS kurs nedostupan")

    for r in fetched:
        table.add(r)
    if db is not None and fetched:
        await db.cache_set(CACHE_KEY, [r.model_dump(mode="json") for r in fetched], ttl_seconds)
    status = "Živi kursevi (ECB + NBS)." if not messages else "; ".join(messages) + " – delimično rezervni kursevi."
    return table, status
