import type { TripPackage } from "../types";
import { fmtMoney, localDate, money } from "../format";
import { BathroomBadge, OriginBadge, Pass, Score } from "./Badges";
import { CostTable } from "./CostTable";

type Props = {
  pkg: TripPackage;
  onItinerary?: () => void;
  onSave?: () => void;
  saveState?: "idle" | "saving" | "saved";
  compareChecked?: boolean;
  onCompare?: (checked: boolean) => void;
  compareDisabled?: boolean;
};

export function TripCard({ pkg, onItinerary, onSave, saveState = "idle", compareChecked, onCompare, compareDisabled }: Props) {
  const f = pkg.flight;
  const o = pkg.accommodation.offer;
  const cur = pkg.cost.currency;
  const agentNote = pkg.highlights.find((h) => h.startsWith("Agent:"))?.replace(/^Agent:\s*/, "");
  const reasons = pkg.highlights.filter((h) => !h.startsWith("Agent:"));
  return (
    <article className="card trip" aria-label={`${pkg.destination_name}, ${pkg.rank}. mesto`}>
      <header className="trip-head">
        <div className="trip-title">
          {pkg.rank ? <span className="rank">{pkg.rank}.</span> : null}
          <div>
            <h3>{pkg.destination_name}</h3>
            <div className="small muted">
              {localDate(f.outbound.departure_local)} – {localDate(f.inbound.departure_local)} · {pkg.usable_time.nights} noći ·{" "}
              {pkg.usable_time.usable_hours.toFixed(0)} h za obilazak
            </div>
            <div className="row" style={{ marginTop: 6, gap: 14 }}>
              {pkg.data_origins.map((d) => (
                <OriginBadge key={d} origin={d} />
              ))}
              {pkg.cost.is_complete ? (
                <span className="badge ok">Sve cene poznate</span>
              ) : (
                <span className="badge bad" title={pkg.cost.unknown_items.join("; ")}>
                  Deo cene nepoznat
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="price-block">
          <div className="big tnum">{money(pkg.cost.known_total, cur)}</div>
          <div className="small muted">
            {money(pkg.cost.per_person_known, cur)} po osobi · let + smeštaj
          </div>
          <div className="tiny muted">sa procenom troškova na licu mesta ≈ {money(pkg.cost.comparable_total, cur)}</div>
        </div>
      </header>

      <div className="trip-body">
        <section>
          <div className="section-label">
            <span className="eyebrow">Let · {f.airline}</span>
            <span className="small tnum">{money(f.total_fare.amount, f.total_fare.currency)}</span>
          </div>
          <Pass leg={f.outbound} label="Tamo" />
          <Pass leg={f.inbound} label="Nazad" />
          <dl className="kv">
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
            <dd>{f.fare_type === "regular" ? "Redovna (bez Discount Club članstva)" : "Članska"}</dd>
          </dl>
        </section>
        <section>
          <div className="section-label">
            <span className="eyebrow">Smeštaj · {o.property_type ?? "tip nepoznat"}</span>
            <span className="small tnum">{fmtMoney(o.total_price, 0)}</span>
          </div>
          <div className="stay-name">{o.property_name}</div>
          <div className="row" style={{ gap: 14, marginTop: 4 }}>
            <Score score={o.review_score} count={o.review_count} />
            <BathroomBadge status={o.bathroom} />
          </div>
          <dl className="kv">
            <dt>Soba</dt>
            <dd>
              {o.room_name ?? "—"} · do {o.max_occupancy ?? "?"} osobe{o.rooms > 1 ? ` · ${o.rooms} sobe` : ""}
            </dd>
            <dt>Noćenja</dt>
            <dd>
              {localDate(o.checkin)} → {localDate(o.checkout)}
            </dd>
            {o.distance_km ? (
              <>
                <dt>Lokacija</dt>
                <dd>
                  {o.distance_km} km od {o.distance_reference ?? "centra"}
                </dd>
              </>
            ) : null}
            <dt>Otkazivanje</dt>
            <dd>{o.cancellation ?? "nepoznato"}</dd>
            <dt>Plaćanje</dt>
            <dd>{o.prepayment ?? "nepoznato"}</dd>
          </dl>
        </section>
      </div>

      <div className="trip-notes stack" style={{ gap: 8 }}>
        {agentNote ? <p className="small" style={{ margin: 0, color: "var(--ink-2)" }}>{agentNote}</p> : null}
        {pkg.caveats.length ? (
          <ul className="caveats" style={{ marginTop: 0 }}>
            {pkg.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : null}
        <div className="row" style={{ gap: 20 }}>
          <details>
            <summary>Svi troškovi</summary>
            <CostTable cost={pkg.cost} />
          </details>
          <details>
            <summary>Zašto je na ovom mestu</summary>
            <ul className="highlights">
              {reasons.map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
          </details>
        </div>
      </div>

      <footer className="trip-foot">
        {onSave ? (
          <button className="btn primary" onClick={onSave} disabled={saveState !== "idle"}>
            {saveState === "saving" ? "Čuvam i pravim plan…" : saveState === "saved" ? "Sačuvano ✓" : "Sačuvaj putovanje"}
          </button>
        ) : null}
        {onItinerary ? (
          <button className="btn" onClick={onItinerary}>
            Plan obilaska
          </button>
        ) : null}
        {f.booking_url ? (
          <a className="btn ghost" href={f.booking_url} target="_blank" rel="noopener noreferrer">
            Wizz Air ↗
          </a>
        ) : null}
        {o.source_url ? (
          <a className="btn ghost" href={o.source_url} target="_blank" rel="noopener noreferrer">
            Booking.com ↗
          </a>
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
