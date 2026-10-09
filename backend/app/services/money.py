"""Decimal money arithmetic and currency conversion.

All conversion goes through EUR using an `FxTable` snapshot whose rates carry their
source and date, so every converted total can show exactly which rates were used.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.models import FxRate, Money

CENT = Decimal("0.01")

# Last-resort rates, only used when live rates cannot be fetched. Clearly flagged as fallback.
FALLBACK_AS_OF = date(2026, 10, 8)
FALLBACK_EUR_RATES: dict[str, Decimal] = {
    "EUR": Decimal("1"),
    "RSD": Decimal("117.3657"),
    "GBP": Decimal("0.87"),
    "HUF": Decimal("366.25"),
    "PLN": Decimal("4.3753"),
    "RON": Decimal("5.09"),
    "BGN": Decimal("1.95583"),
    "CHF": Decimal("0.93"),
    "SEK": Decimal("11.0"),
    "NOK": Decimal("11.6"),
    "DKK": Decimal("7.46"),
    "CZK": Decimal("24.3"),
    "MKD": Decimal("61.5"),
    "BAM": Decimal("1.95583"),
    "ALL": Decimal("97.0"),
    "ILS": Decimal("3.9"),
    "AED": Decimal("4.3"),
    "TRY": Decimal("48.0"),
    "GEL": Decimal("3.15"),
}


class CurrencyError(ValueError):
    pass


def q(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money(amount: str | int | Decimal, currency: str) -> Money:
    return Money(amount=Decimal(str(amount)), currency=currency)


@dataclass
class FxTable:
    """Rates expressed as: 1 EUR = rate[currency] units of currency."""

    rates: dict[str, FxRate] = field(default_factory=dict)

    @classmethod
    def fallback(cls) -> FxTable:
        table = cls()
        for cur, r in FALLBACK_EUR_RATES.items():
            table.rates[cur] = FxRate(
                base="EUR",
                quote=cur,
                rate=r,
                source="static fallback (not current)",
                as_of=FALLBACK_AS_OF,
                is_fallback=True,
            )
        return table

    def add(self, rate: FxRate) -> None:
        if rate.base != "EUR":
            raise CurrencyError("FxTable stores EUR-based rates only")
        if rate.rate <= 0:
            raise CurrencyError("rate must be positive")
        self.rates[rate.quote] = rate

    def rate(self, currency: str) -> FxRate:
        if currency == "EUR":
            return FxRate(base="EUR", quote="EUR", rate=Decimal("1"), source="identity", as_of=date.today())
        try:
            return self.rates[currency]
        except KeyError as exc:
            raise CurrencyError(f"no exchange rate for {currency}") from exc

    def convert(self, m: Money, to: str) -> Money:
        if m.currency == to:
            return m
        eur = m.amount if m.currency == "EUR" else m.amount / self.rate(m.currency).rate
        out = eur if to == "EUR" else eur * self.rate(to).rate
        return Money(amount=q(out), currency=to)

    def used(self, currencies: Iterable[str]) -> list[FxRate]:
        return [self.rate(c) for c in sorted(set(currencies)) if c != "EUR"]


def sum_money(items: Iterable[Money], currency: str, fx: FxTable) -> Money:
    total = Decimal("0")
    for m in items:
        total += fx.convert(m, currency).amount
    return Money(amount=q(total), currency=currency)
