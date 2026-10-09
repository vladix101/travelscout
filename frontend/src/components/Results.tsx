import { useMemo, useState } from "react";
import type { FlightOption, MailStatus, SearchResult, TripPackage } from "../types";
import { BATHROOM_LABEL, RULE_LABEL, fmtMoney, localDate, money } from "../format";
import { Score } from "./Badges";
import { TripCard } from "./TripCard";

type Filters = {
  maxPerPerson: string;
  destination: string;
  baggage: "all" | "known";
  minScore: number;
  maxDistance: string;
  sort: "rank" | "price" | "score";
};

const INITIAL: Filters = { maxPerPerson: "", destination: "", baggage: "all", minScore: 7.5, maxDistance: "", sort: "rank" };

function bookingSearchLink(f: FlightOption, r: SearchResult, city: string): string {
  const c = r.constraints;
  const checkin = f.outbound.departure_local.slice(0, 10);
  const checkout = f.inbound.departure_local.slice(0, 10);
  const params = new URLSearchParams({
    ss: city,
    checkin,
    checkout,
    group_adults: String(c.party.adults),
    group_children: String(c.party.children),
    no_rooms: String(c.rooms),
    selected_currency: "EUR",
  });
  c.party.child_ages.forEach((a) => params.append("age", String(a)));
  return `https://www.booking.com/searchresults.html?${params.toString()}`;
}

type Props = {
  result: SearchResult;
  jobId: string;
  onItinerary: (pkg: TripPackage) => void;
  onSave: (pkg: TripPackage) => void;
  saveStates: Record<string, "idle" | "saving" | "saved">;
  mail: MailStatus | null;
  onSendMail: (pkg: TripPackage, to: string) => Promise<{ to: string }>;
  onCompare: (pkgs: TripPackage[]) => void;
};

export function Results({ result, onItinerary, onSave, saveStates, onCompare, mail, onSendMail }: Props) {
  const [filters, setFilters] = useState<Filters>(INITIAL);
  const [compare, setCompare] = useState<string[]>([]);
  const cur = result.constraints.display_currency;

  const destinations = useMemo(
    () => Array.from(new Map(result.packages.map((p) => [p.destination, p.destination_name])).entries()),
    [result.packages],
  );

  const shown = useMemo(() => {
    let list = result.packages.filter((p) => {
      if (filters.maxPerPerson && Number(p.cost.per_person_known) > Number(filters.maxPerPerson)) return false;
      if (filters.destination && p.destination !== filters.destination) return false;
      if (filters.baggage === "known" && p.flight.baggage_quotes.some((b) => b.unit_price === null)) return false;
      const s = p.accommodation.offer.review_score;
      if (s !== null && Number(s) < filters.minScore) return false;
      const d = p.accommodation.offer.distance_km;
      if (filters.maxDistance && (d === null || Number(d) > Number(filters.maxDistance))) return false;
      return true;
    });
    if (filters.sort === "price") list = [...list].sort((a, b) => Number(a.cost.known_total) - Number(b.cost.known_total));
    if (filters.sort === "score")
      list = [...list].sort((a, b) => Number(b.accommodation.offer.review_score ?? 0) - Number(a.accommodation.offer.review_score ?? 0));
    return list;
  }, [result.packages, filters]);

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const comparePkgs = result.packages.filter((p) => compare.includes(p.id));
  const rejectedTotal = Object.values(result.rejected_counts).reduce((a, b) => a + b, 0);
  const hasPackages = result.packages.length > 0;

  return (
    <div className="stack">
      {result.mode === "demo" ? (
        <div className="notice demo small">
          <strong>DEMO podaci.</strong> Letovi i smeštaj su izmišljeni radi isprobavanja celog toka – nisu stvarne ponude i ne mogu se rezervisati.
        </div>
      ) : null}
      {result.partial ? (
        <div className="notice warn small">Rezultati su delimični (otkazano, isteklo vreme ili je neki izvor podataka bio nedostupan).</div>
      ) : null}

      <section className="card panel stack">
        <div>
          <div className="eyebrow">Korak 2 · smeštaj za izabrane letove</div>
          <h2>
            {hasPackages
              ? `${result.packages.length} ${result.packages.length === 1 ? "putovanje ispunjava" : "putovanja ispunjavaju"} sve uslove`
              : "Nijedno putovanje ne ispunjava sve uslove"}
          </h2>
          {rejectedTotal > 0 ? (
            <p className="small muted" style={{ margin: "4px 0 0" }}>
              Odbijeno ponuda smeštaja: {rejectedTotal} (
              {Object.entries(result.rejected_counts)
                .map(([k, v]) => `${RULE_LABEL[k] ?? k} ${v}`)
                .join(", ")}
              )
            </p>
          ) : null}
        </div>

        {result.diagnostics.length || result.suggestions.length ? (
          <div className={`notice ${hasPackages ? "info" : "warn"} small`}>
            {result.diagnostics.map((d, i) => (
              <div key={i}>
                {d.message}
                {d.count ? ` (${d.count})` : ""}
              </div>
            ))}
            {result.suggestions.length ? (
              <>
                <strong>Šta možete promeniti (ništa se ne menja automatski):</strong>
                <ul>
                  {result.suggestions.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}

        {result.packages.length > 1 ? (
          <details>
          <summary className="muted">Filteri i sortiranje</summary>
          <div className="filters" role="group" aria-label="Filteri">
            <label className="field">
              Maks. po osobi ({cur})
              <input type="number" min={0} value={filters.maxPerPerson} placeholder="sve" onChange={(e) => set({ maxPerPerson: e.target.value })} />
            </label>
            <label className="field">
              Destinacija
              <select value={filters.destination} onChange={(e) => set({ destination: e.target.value })}>
                <option value="">Sve</option>
                {destinations.map(([code, name]) => (
                  <option key={code} value={code}>
                    {name} ({code})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Prtljag
              <select value={filters.baggage} onChange={(e) => set({ baggage: e.target.value as Filters["baggage"] })}>
                <option value="all">Svi paketi</option>
                <option value="known">Samo sa poznatom cenom prtljaga</option>
              </select>
            </label>
            <label className="field">
              Min. ocena: {filters.minScore.toFixed(1)}
              <input type="range" min={7.5} max={10} step={0.1} value={filters.minScore} onChange={(e) => set({ minScore: Number(e.target.value) })} />
            </label>
            <label className="field">
              Maks. km od centra
              <input type="number" min={0} step={0.5} value={filters.maxDistance} placeholder="sve" onChange={(e) => set({ maxDistance: e.target.value })} />
            </label>
            <label className="field">
              Sortiraj
              <select value={filters.sort} onChange={(e) => set({ sort: e.target.value as Filters["sort"] })}>
                <option value="rank">Po rangu</option>
                <option value="price">Po poznatoj ceni</option>
                <option value="score">Po oceni smeštaja</option>
              </select>
            </label>
          </div>
          </details>
        ) : null}
      </section>

      {hasPackages && shown.length === 0 ? (
        <div className="card empty">
          <h2>Nijedan paket ne odgovara filterima</h2>
          <button className="btn" onClick={() => setFilters(INITIAL)}>
            Poništi filtere
          </button>
        </div>
      ) : null}

      <div className="trip-list">
        {shown.map((p) => (
          <TripCard
            key={p.id}
            pkg={p}
            onItinerary={() => onItinerary(p)}
            onSave={() => onSave(p)}
            saveState={saveStates[p.id] ?? "idle"}
            mail={mail}
            onSendMail={(to) => onSendMail(p, to)}
            compareChecked={compare.includes(p.id)}
            compareDisabled={compare.length >= 3}
            onCompare={(checked) => setCompare((c) => (checked ? [...c, p.id].slice(0, 3) : c.filter((x) => x !== p.id)))}
          />
        ))}
      </div>

      {compare.length > 0 ? (
        <div className="sticky-bar">
          <strong>Za poređenje: {compare.length}/3</strong>
          <span className="small muted">{comparePkgs.map((p) => p.destination_name).join(", ")}</span>
          <button className="btn small primary" disabled={compare.length < 2} onClick={() => onCompare(comparePkgs)} style={{ marginLeft: "auto" }}>
            Uporedi
          </button>
          <button className="btn small ghost" onClick={() => setCompare([])}>
            Očisti
          </button>
        </div>
      ) : null}

      {!hasPackages && result.flight_shortlist.length > 0 ? (
        <section className="card panel stack">
          <h2>Pronađeni letovi bez potvrđenog smeštaja</h2>
          <p className="small muted" style={{ margin: 0 }}>
            Ovo nisu kompletni paketi. Smeštaj koji ispunjava sve uslove nije potvrđen – proverite ga direktno kod provajdera.
          </p>
          {result.flight_shortlist.map((f) => (
            <div key={f.id} className="row spread" style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              <div className="small">
                <strong>
                  {f.outbound.origin} → {f.outbound.destination}
                </strong>{" "}
                {localDate(f.outbound.departure_local)} – {localDate(f.inbound.departure_local)} · {money(f.total_fare.amount, f.total_fare.currency)} za
               
                sve putnike
              </div>
              <div className="row">
                {f.booking_url ? (
                  <a className="btn small" href={f.booking_url} target="_blank" rel="noopener noreferrer">
                    Wizz Air ↗
                  </a>
                ) : null}
                <a className="btn small" href={bookingSearchLink(f, result, result.destination_cities[f.outbound.destination] ?? f.outbound.destination)} target="_blank" rel="noopener noreferrer">
                  Booking.com pretraga ↗
                </a>
              </div>
            </div>
          ))}
        </section>
      ) : null}

      {result.needs_verification.length > 0 ? (
        <section className="card panel stack">
          <details>
            <summary>
              Potrebna provera ({result.needs_verification.length}) – ponude kojima nedostaje dokaz za neki obavezni uslov
            </summary>
            <div className="stack">
              {result.needs_verification.map((a) => (
                <div key={a.offer.id} className="row spread" style={{ borderTop: "1px solid var(--border)", paddingTop: 8, alignItems: "flex-start" }}>
                  <div className="stack" style={{ gap: 4 }}>
                    <strong>{a.offer.property_name}</strong>
                    <span className="small muted">
                      {a.offer.room_name ?? "soba nepoznata"} · {localDate(a.offer.checkin)} → {localDate(a.offer.checkout)} · {fmtMoney(a.offer.total_price)}
                    </span>
                    <div className="row" style={{ gap: 6 }}>
                      <Score score={a.offer.review_score} count={a.offer.review_count} />
                      <span className="badge neutral">{BATHROOM_LABEL[a.offer.bathroom]}</span>
                      {a.eligibility.unverified.map((u) => (
                        <span key={u} className="badge warn">
                          {RULE_LABEL[u] ?? u}
                        </span>
                      ))}
                    </div>
                  </div>
                  {a.offer.source_url ? (
                    <a className="btn small" href={a.offer.source_url} target="_blank" rel="noopener noreferrer">
                      Proveri ↗
                    </a>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        </section>
      ) : null}
    </div>
  );
}
