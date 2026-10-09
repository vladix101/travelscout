import type { BathroomStatus, DataOrigin } from "../types";
import { BATHROOM_LABEL, ORIGIN_LABEL, timestamp } from "../format";

const ORIGIN_TITLE: Record<DataOrigin, string> = {
  live: "Preuzeto od provajdera tokom ove pretrage",
  cached: "Preuzeto ranije od provajdera, iz lokalnog keša",
  manual: "Uneo korisnik – nije automatski provereno",
  demo: "Izmišljeni demo podaci – nisu stvarne ponude",
  unverified: "Trag za istraživanje, ne potvrđena ponuda",
};

export function OriginBadge({ origin, at }: { origin: DataOrigin; at?: string | null }) {
  return (
    <span className={`badge ${origin}`} title={ORIGIN_TITLE[origin] + (at ? ` · ${timestamp(at)}` : "")}>
      {ORIGIN_LABEL[origin]}
      {at && origin !== "demo" ? <span style={{ fontWeight: 500 }}> · {timestamp(at)}</span> : null}
    </span>
  );
}

export function BathroomBadge({ status }: { status: BathroomStatus }) {
  const tone = status === "shared" ? "bad" : status === "unknown" ? "warn" : status === "private_external" ? "warn" : "ok";
  return <span className={`badge ${tone}`}>{BATHROOM_LABEL[status]}</span>;
}

export function ScoreBadge({ score, count }: { score: string | null; count: number | null }) {
  if (score === null) return <span className="badge warn">Ocena nepoznata</span>;
  const n = Number(score);
  const tone = n >= 8.5 ? "ok" : n >= 7.5 ? "neutral" : "bad";
  return (
    <span className={`badge ${tone}`} title="Booking.com ocena gostiju">
      ★ {n.toFixed(1)}
      {count ? <span style={{ fontWeight: 500 }}> · {count.toLocaleString("sr-Latn-RS")} recenzija</span> : null}
    </span>
  );
}
