import type { BathroomStatus, DataOrigin, FlightLeg } from "../types";
import { BATHROOM_LABEL, ORIGIN_LABEL, localDay, localTime, timestamp } from "../format";

const ORIGIN_TITLE: Record<DataOrigin, string> = {
  live: "Preuzeto od provajdera tokom ove pretrage",
  cached: "Preuzeto ranije od provajdera, iz lokalnog keša",
  manual: "Uneo korisnik – nije automatski provereno",
  demo: "Izmišljeni demo podaci – nisu stvarne ponude",
  unverified: "Trag za istraživanje, ne potvrđena ponuda",
};

export function OriginBadge({ origin, at }: { origin: DataOrigin; at?: string | null }) {
  return (
    <span className={`badge ${origin}`} title={ORIGIN_TITLE[origin]}>
      {ORIGIN_LABEL[origin]}
      {at && origin !== "demo" ? <span className="when">{timestamp(at)}</span> : null}
    </span>
  );
}

export function BathroomBadge({ status }: { status: BathroomStatus }) {
  const tone = status === "shared" ? "bad" : status === "unknown" || status === "private_external" ? "warn" : "ok";
  return <span className={`badge ${tone}`}>{BATHROOM_LABEL[status]}</span>;
}

export function Score({ score, count }: { score: string | null; count: number | null }) {
  if (score === null) return <span className="badge warn">Ocena nepoznata</span>;
  return (
    <span className="small" title="Booking.com ocena gostiju">
      <strong className="tnum">{Number(score).toFixed(1)}</strong>
      <span className="muted"> / 10{count ? ` · ${count.toLocaleString("sr-Latn-RS")} recenzija` : ""}</span>
    </span>
  );
}

/** A flight leg drawn like a boarding pass: big times, airport codes, a hairline route. */
export function Pass({ leg, label }: { leg: FlightLeg; label: string }) {
  return (
    <div className="pass">
      <div>
        <div className="time">{localTime(leg.departure_local)}</div>
        <div className="code">{leg.origin}</div>
      </div>
      <div className="track" aria-hidden>
        <span>
          {label} · {localDay(leg.departure_local)}
        </span>
      </div>
      <div className="end">
        <div className="time">
          {leg.arrival_local ? localTime(leg.arrival_local) : "—"}
          {leg.arrival_is_estimate && leg.arrival_local ? <sup className="tiny muted" title="Procena"> ~</sup> : null}
        </div>
        <div className="code">{leg.destination}</div>
      </div>
      <div className="meta">
        {leg.flight_number ? `Let ${leg.flight_number} · ` : ""}lokalno vreme
        {leg.arrival_is_estimate ? " · ~ sletanje je procena" : ""}
      </div>
    </div>
  );
}
