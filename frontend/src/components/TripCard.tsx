import type { FlightLeg, TripPackage } from "../types";
import { fmtMoney, localDate, localDay, localTime, money, tzShort } from "../format";
import { BathroomBadge, OriginBadge, ScoreBadge } from "./Badges";
import { CostTable } from "./CostTable";

function Leg({ leg, label }: { leg: FlightLeg; label: string }) {
  return (
    <div className="leg">
      <span className="time">{localTime(leg.departure_local)}</span>
      <span>
        <strong>
          {label}: {leg.origin} → {leg.destination}
        </strong>{" "}
        · {localDay(leg.departure_local)} <span className="tiny muted">({tzShort(leg.departure_tz)})</span>
      </span>
      <span className="time">{leg.arrival_local ? localTime(leg.arrival_local) : "—"}</span>
      <span className="small">
        Dolazak{leg.arrival_is_estimate ? <span className="badge warn" style={{ marginLeft: 6 }}>procena</span> : null}{" "}
        <span className="tiny muted">({tzShort(leg.arrival_tz)})</span> · {leg.flight_number ?? "broj leta nepoznat"} ·{" "}
        {fmtMoney(leg.fare_per_seat)} po sedištu
      </span>
    </div>
  );
}

type Props = {
  pkg: TripPackage;
  onItinerary?: () => void;
  onFavorite?: () => void;
  favoriteLabel?: string;
  compareChecked?: boolean;
  onCompare?: (checked: boolean) => void;
  compareDisabled?: boolean;
  exportLinks?: { md: string; json: string } | null;
};

export function TripCard({ pkg, onItinerary, onFavorite, favoriteLabel, compareChecked, onCompare, compareDisabled, exportLinks }: Props) {
  const f = pkg.flight;
  const o = pkg.accommodation.offer;
  const cur = pkg.cost.currency;
  return (
    <article className="card trip" aria-label={`${pkg.destination_name}, rang ${pkg.rank}`}>
      <header className="trip-head">
        <div className="trip-title">
          {pkg.rank ? <span className="rank">{pkg.rank}</span> : null}
          <div>
            <h3>
              {pkg.destination_name}
              {pkg.country ? <span className="muted" style={{ fontWeight: 500 }}>, {pkg.country}</span> : null}
            </h3>
            <div className="small muted">
              {localDate(f.outbound.departure_local)} – {localDate(f.inbound.departure_local)} · {pkg.usable_time.nights} noći ·{" "}
              {f.outbound.origin}⇄{pkg.destination}
            </div>
            <div className="row" style={{ marginTop: 4, gap: 6 }}>
              {pkg.data_origins.map((d) => (
                <OriginBadge key={d} origin={d} at={d === f.evidence.origin ? f.evidence.retrieved_at : o.evidence.retrieved_at} />
              ))}
              {pkg.cost.is_complete ? (
                <span className="badge ok">Sve cene poznate</span>
              ) : (
                <span className="badge bad" title={pkg.cost.unknown_items.join("; ")}>
                  Nepotpuna cena
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="price-block">
          <div className="big">{money(pkg.cost.known_total, cur)}</div>
          <div className="small muted">poznati troškovi · {money(pkg.cost.per_person_known, cur)} po osobi</div>
          <div className="tiny muted">sa procenama ≈ {money(pkg.cost.comparable_total, cur)}</div>
        </div>
      </header>

      <div className="trip-body">
        <section>
          <div className="section-label">
            Letovi · {f.airline} <OriginBadge origin={f.evidence.origin} at={f.evidence.retrieved_at} />
          </div>
          <Leg leg={f.outbound} label="Tamo" />
          <Leg leg={f.inbound} label="Nazad" />
          <dl className="kv" style={{ marginTop: 6 }}>
            <dt>Ukupno za let</dt>
            <dd>
              {f.total_fare_by_leg.map((m) => fmtMoney(m)).join(" + ")} ({f.passengers.map((p) => `${p.count}× ${p.passenger_type === "adult" ? "odrasli" : p.passenger_type === "child" ? "dete" : "beba"}`).join(", ")})
            </dd>
            <dt>Prtljag</dt>
            <dd>
              {f.included_baggage}
              {f.baggage_quotes.map((b) => (
                <div key={b.item} className="tiny">
                  + {b.item} ×{b.quantity}: {b.unit_price ? `${fmtMoney(b.unit_price)} / kom` : <span style={{ color: "var(--bad)" }}>cena nepoznata</span>}
                </div>
              ))}
            </dd>
            <dt>Tarifa</dt>
            <dd>{f.fare_type === "regular" ? "Redovna (bez Wizz Discount Club članstva)" : "Članska tarifa"}</dd>
          </dl>
        </section>
        <section>
          <div className="section-label">
            Smeštaj · {o.property_type ?? "tip nepoznat"} <OriginBadge origin={o.evidence.origin} at={o.evidence.retrieved_at} />
          </div>
          <div className="stack" style={{ gap: 6 }}>
            <strong>{o.property_name}</strong>
            <div className="row" style={{ gap: 6 }}>
              <ScoreBadge score={o.review_score} count={o.review_count} />
              <BathroomBadge status={o.bathroom} />
            </div>
            <dl className="kv">
              <dt>Soba</dt>
              <dd>
                {o.room_name ?? "—"} · do {o.max_occupancy ?? "?"} osoba{o.rooms > 1 ? ` · ${o.rooms} sobe` : ""}
              </dd>
              <dt>Kupatilo</dt>
              <dd className="small">{o.bathroom_evidence}</dd>
              <dt>Boravak</dt>
              <dd>
                {localDate(o.checkin)} → {localDate(o.checkout)} ({pkg.usable_time.nights} noći) · {fmtMoney(o.total_price)}
              </dd>
              {o.distance_km ? (
                <>
                  <dt>Udaljenost</dt>
                  <dd>
                    {o.distance_km} km od {o.distance_reference ?? "centra"}
                  </dd>
                </>
              ) : null}
              <dt>Otkazivanje</dt>
              <dd className="small">{o.cancellation ?? "nepoznato"}</dd>
              <dt>Plaćanje</dt>
              <dd className="small">{o.prepayment ?? "nepoznato"}</dd>
            </dl>
          </div>
        </section>
      </div>

      <div style={{ padding: "10px 16px", borderTop: "1px solid var(--border)" }} className="stack">
        <ul className="highlights">
          {pkg.highlights.map((h) => (
            <li key={h}>{h}</li>
          ))}
        </ul>
        {pkg.caveats.length ? (
          <ul className="caveats">
            {pkg.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : null}
        <details>
          <summary>Pregled troškova</summary>
          <CostTable cost={pkg.cost} />
        </details>
      </div>

      <footer className="trip-foot">
        {onItinerary ? (
          <button className="btn small primary" onClick={onItinerary}>
            Plan putovanja
          </button>
        ) : null}
        {f.booking_url ? (
          <a className="btn small" href={f.booking_url} target="_blank" rel="noopener noreferrer">
            Wizz Air ↗
          </a>
        ) : null}
        {o.source_url ? (
          <a className="btn small" href={o.source_url} target="_blank" rel="noopener noreferrer">
            Booking.com ↗
          </a>
        ) : null}
        {onFavorite ? (
          <button className="btn small" onClick={onFavorite}>
            {favoriteLabel ?? "☆ Omiljeno"}
          </button>
        ) : null}
        {exportLinks ? (
          <>
            <a className="btn small ghost" href={exportLinks.md} download>
              Markdown
            </a>
            <a className="btn small ghost" href={exportLinks.json} download>
              JSON
            </a>
          </>
        ) : null}
        {onCompare ? (
          <label className="check" style={{ marginLeft: "auto" }}>
            <input type="checkbox" checked={!!compareChecked} disabled={compareDisabled && !compareChecked} onChange={(e) => onCompare(e.target.checked)} />
            Uporedi
          </label>
        ) : null}
      </footer>
    </article>
  );
}
