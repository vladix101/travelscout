"""E-mail a saved trip through Gmail SMTP (app password from .env).

The HTML body is built from typed data; every value that originated outside the app
(property names, attraction names, notes) is HTML-escaped. The password is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from app.config import Settings
from app.domain.models import Itinerary, TripPackage
from app.services.export import BATHROOM_LABEL, ORIGIN_LABEL, package_to_json, package_to_markdown

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
CATEGORY_LABEL = {"mandatory": "obavezno", "extra": "dodatak", "estimate": "procena", "unknown": "nepoznato"}
BOX = "background:#fff;border:1px solid #e4ddd1;padding:16px;margin-bottom:14px"
BODY_FONT = "font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#1d1b18"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class MailError(RuntimeError):
    """Safe-to-display e-mail error."""


def mail_configured(settings: Settings) -> bool:
    return bool(settings.gmail_address and settings.google_app_password and app_password(settings))


def app_password(settings: Settings) -> str:
    # Google shows app passwords as 4 groups of 4 letters; spaces are not part of the password.
    raw = settings.google_app_password.get_secret_value() if settings.google_app_password else ""
    return re.sub(r"\s+", "", raw)


def valid_email(addr: str) -> bool:
    return bool(EMAIL_RE.match(addr)) and len(addr) <= 254


def _money(amount: object, cur: str) -> str:
    return f"{float(str(amount)):,.2f} {cur}".replace(",", " ")


def render_html(pkg: TripPackage, it: Itinerary | None, note: str | None = None) -> str:
    f, o, c = pkg.flight, pkg.accommodation.offer, pkg.cost
    e = escape

    def leg_row(label: str, leg) -> str:  # type: ignore[no-untyped-def]
        arr = leg.arrival_local.strftime("%H:%M") if leg.arrival_local else "—"
        est = " (procena)" if leg.arrival_is_estimate and leg.arrival_local else ""
        return (
            f"<tr><td style='padding:6px 0;color:#6b6358'>{e(label)}</td>"
            f"<td style='padding:6px 0'><b>{leg.departure_local:%d.%m. %H:%M}</b> {e(leg.origin)} → "
            f"{e(leg.destination)} {arr}{est}</td>"
            f"<td style='padding:6px 0;text-align:right'>{_money(leg.fare_per_seat.amount, leg.fare_per_seat.currency)}"
            "/sedište</td></tr>"
        )

    cost_rows = "".join(
        f"<tr><td style='padding:4px 0'>{e(ln.label)}</td><td style='padding:4px 0;color:#6b6358'>"
        f"{e(CATEGORY_LABEL[ln.category])}"
        f"</td><td style='padding:4px 0;text-align:right'>"
        f"{_money(ln.amount.amount, ln.amount.currency) if ln.amount else '?'}</td></tr>"
        for ln in c.lines
    )
    days = ""
    if it:
        for d in it.days:
            items = "".join(
                f"<li style='margin:2px 0'><span style='color:#6b6358'>{e(i.start)}–{e(i.end)}</span> "
                f"{'<b>' + e(i.title) + '</b>' if i.kind == 'sight' else e(i.title)}"
                + (f" · <a href='{e(i.map_url)}'>mapa</a>" if i.map_url else "")
                + ("" if i.verified or i.kind != "sight" else " <i style='color:#a15c00'>(neprovereno)</i>")
                + "</li>"
                for i in d.items
            )
            days += (
                f"<h3 style='font:600 15px Georgia,serif;margin:18px 0 4px'>{d.date:%d.%m.} – {e(d.title)}</h3>"
                f"<ul style='margin:0;padding-left:18px'>{items}</ul>"
            )
    links = []
    if f.booking_url:
        links.append(f"<a href='{e(f.booking_url)}'>Rezerviši let na wizzair.com</a>")
    if o.source_url:
        links.append(f"<a href='{e(o.source_url)}'>Smeštaj na booking.com</a>")
    origins = ", ".join(ORIGIN_LABEL[x.value] for x in pkg.data_origins)
    unknown = (
        f"<p style='color:#a13a1a'>Cena nije kompletna – nepoznato: {e('; '.join(c.unknown_items))}</p>"
        if c.unknown_items
        else ""
    )
    plan_html = (
        f'<div style="{BOX}"><h2 style="font:600 17px Georgia,serif;margin:0">Plan obilaska</h2>{days}</div>'
        if days
        else ""
    )
    note_html = f"<p style='background:#f3efe7;padding:10px'>{e(note)}</p>" if note else ""
    return f"""<!doctype html><html><body style="margin:0;background:#f6f3ee">
<div style="max-width:640px;margin:0 auto;padding:24px;{BODY_FONT}">
<p style="letter-spacing:.12em;text-transform:uppercase;font-size:11px;color:#6b6358;margin:0">TravelScout · {
        e(origins)
    }</p>
<h1 style="font:600 28px Georgia,serif;margin:4px 0 2px">{e(pkg.destination_name)}{
        ", " + e(pkg.country) if pkg.country else ""
    }</h1>
<p style="margin:0 0 16px;color:#6b6358">{f.outbound.departure_local:%d.%m.%Y} – {f.inbound.departure_local:%d.%m.%Y} ·
{pkg.usable_time.nights} noći</p>
{note_html}
<div style="{BOX}">
<p style="margin:0;font-size:12px;color:#6b6358">Poznati troškovi</p>
<p style="margin:0;font:600 30px Georgia,serif">{_money(c.known_total, c.currency)}</p>
<p style="margin:0;color:#6b6358">{_money(c.per_person_known, c.currency)} po osobi · sa procenama ≈
{_money(c.comparable_total, c.currency)}</p>{unknown}
</div>
<div style="{BOX}">
<h2 style="font:600 17px Georgia,serif;margin:0 0 6px">Let · Wizz Air</h2>
<table style="width:100%;border-collapse:collapse">{leg_row("Tamo", f.outbound)}{leg_row("Nazad", f.inbound)}</table>
<p style="margin:6px 0 0;color:#6b6358">Prtljag: {e(f.included_baggage)}</p>
</div>
<div style="{BOX}">
<h2 style="font:600 17px Georgia,serif;margin:0 0 6px">Smeštaj · {e(o.property_name)}</h2>
<p style="margin:0">{e(o.property_type or "")} · ocena {e(str(o.review_score) if o.review_score is not None else "?")}
· {e(o.room_name or "")}</p>
<p style="margin:0">Kupatilo: {e(BATHROOM_LABEL[o.bathroom.value])}</p>
<p style="margin:0">{o.checkin:%d.%m.} → {o.checkout:%d.%m.%Y} · {
        _money(o.total_price.amount, o.total_price.currency) if o.total_price else "cena nepoznata"
    }</p>
<p style="margin:0;color:#6b6358">Otkazivanje: {e(o.cancellation or "nepoznato")}</p>
</div>
<div style="{BOX}">
<h2 style="font:600 17px Georgia,serif;margin:0 0 6px">Troškovi</h2>
<table style="width:100%;border-collapse:collapse">{cost_rows}</table>
</div>
{plan_html}
<p>{" · ".join(links)}</p>
<p style="font-size:12px;color:#6b6358">Cene i dostupnost proverite pre rezervacije. TravelScout ne vrši rezervacije.
U prilogu su Markdown i JSON verzija putovanja.</p>
</div></body></html>"""


def build_message(
    settings: Settings, to: str, pkg: TripPackage, it: Itinerary | None, note: str | None
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = (
        f"TravelScout: {pkg.destination_name} {pkg.flight.outbound.departure_local:%d.%m.}–"
        f"{pkg.flight.inbound.departure_local:%d.%m.%Y}"
    )
    msg["From"] = formataddr(("TravelScout", settings.gmail_address))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain="travelscout.local")
    md = package_to_markdown(pkg, it)
    msg.set_content(md)
    msg.add_alternative(render_html(pkg, it, note), subtype="html")
    stem = f"travelscout-{pkg.destination.lower()}-{pkg.flight.outbound.departure_local:%Y%m%d}"
    msg.add_attachment(md.encode("utf-8"), maintype="text", subtype="markdown", filename=f"{stem}.md")
    msg.add_attachment(
        package_to_json(pkg, it).encode("utf-8"), maintype="application", subtype="json", filename=f"{stem}.json"
    )
    return msg


def _send_sync(settings: Settings, msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as smtp:
            smtp.login(settings.gmail_address, app_password(settings))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Gmail je odbio prijavu. Proverite GMAIL_ADDRESS i GOOGLE_APP_PASSWORD (16 slova app lozinke, "
            "uz uključenu verifikaciju u 2 koraka)."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Slanje nije uspelo: {type(exc).__name__}") from exc


async def send_trip_email(
    settings: Settings, to: str, pkg: TripPackage, it: Itinerary | None, note: str | None = None
) -> None:
    if not mail_configured(settings):
        raise MailError("Mejl nije podešen: dodajte GMAIL_ADDRESS i GOOGLE_APP_PASSWORD u .env i restartujte server.")
    if not valid_email(to):
        raise MailError("Neispravna adresa primaoca.")
    msg = build_message(settings, to, pkg, it, note)
    await asyncio.to_thread(_send_sync, settings, msg)
    log.info("trip e-mail sent to %s", to)
