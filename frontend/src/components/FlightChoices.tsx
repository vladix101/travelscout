import { useMemo, useState } from "react";
import type { FlightOption, SearchResult } from "../types";
import { localDay, localTime, money, nightsBetween } from "../format";
import { OriginBadge } from "./Badges";

type Props = {
  result: SearchResult;
  maxSelect: number;
  busy: boolean;
  onConfirm: (ids: string[]) => void;
};

function defaultPick(choices: FlightOption[], max: number): string[] {
  // Cheapest option per destination first, up to `max`.
  const sorted = [...choices].sort((a, b) => Number(a.total_fare.amount) - Number(b.total_fare.amount));
  const picked: string[] = [];
  const seen = new Set<string>();
  for (const o of sorted) {
    if (picked.length >= max) break;
    if (!seen.has(o.outbound.destination)) {
      seen.add(o.outbound.destination);
      picked.push(o.id);
    }
  }
  // Fill remaining slots with the next cheapest options (e.g. other date combinations for one destination).
  for (const o of sorted) {
    if (picked.length >= max) break;
    if (!picked.includes(o.id)) picked.push(o.id);
  }
  return picked;
}

export function FlightChoices({ result, maxSelect, busy, onConfirm }: Props) {
  const choices = result.flight_choices;
  const [selected, setSelected] = useState<string[]>(() => defaultPick(choices, Math.min(3, maxSelect)));
  const [sort, setSort] = useState<"date" | "price">("date");
  const people = result.constraints.party.adults + result.constraints.party.children;

  const groups = useMemo(() => {
    const map = new Map<string, FlightOption[]>();
    for (const o of choices) {
      const k = o.outbound.destination;
      map.set(k, [...(map.get(k) ?? []), o]);
    }
    const out = Array.from(map.entries()).map(([dest, opts]) => {
      const list = [...opts].sort((a, b) =>
        sort === "price"
          ? Number(a.total_fare.amount) - Number(b.total_fare.amount)
          : a.outbound.departure_local.localeCompare(b.outbound.departure_local) || a.inbound.departure_local.localeCompare(b.inbound.departure_local),
      );
      const cheapest = Math.min(...opts.map((o) => Number(o.total_fare.amount)));
      return { dest, list, cheapest };
    });
    return out.sort((a, b) => a.cheapest - b.cheapest);
  }, [choices, sort]);

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= maxSelect ? s : [...s, id]));

  if (choices.length === 0) {
    return (
      <div className="card empty">
        <h2>Nema letova za zadate uslove</h2>
        <p className="muted">Proširite period polaska ili raspon noći.</p>
      </div>
    );
  }

  return (
    <section className="card panel stack" aria-label="Izbor letova">
      <div className="row spread">
        <div>
          <h2>Korak 1: izaberite letove</h2>
          <p className="small muted" style={{ margin: 0 }}>
            Pronađeno {choices.length} kombinacija letova. Izaberite do {maxSelect}; smeštaj tražimo tačno za datume izabranih letova.
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Sortiranje letova">
          <button type="button" aria-pressed={sort === "date"} onClick={() => setSort("date")}>
            Po datumu
          </button>
          <button type="button" aria-pressed={sort === "price"} onClick={() => setSort("price")}>
            Po ceni
          </button>
        </div>
      </div>
      {result.mode === "demo" ? <div className="notice demo small">DEMO letovi – nisu stvarne ponude.</div> : null}
      {result.agent_notes.map((n) => (
        <div key={n} className="small muted">
          {n}
        </div>
      ))}

      {groups.map(({ dest, list }) => (
        <div key={dest} className="stack" style={{ gap: 6 }}>
          <h3>
            {result.destination_cities[dest] ?? dest} <span className="muted" style={{ fontWeight: 500 }}>({dest})</span>
          </h3>
          <div className="compare">
            <table>
              <thead>
                <tr>
                  <th />
                  <th>Polazak</th>
                  <th>Povratak</th>
                  <th>Trajanje</th>
                  <th className="num">Cena letova</th>
                  <th>Izvor</th>
                </tr>
              </thead>
              <tbody>
                {list.map((o) => {
                  const nights = nightsBetween(o.outbound.departure_local, o.inbound.departure_local);
                  const checked = selected.includes(o.id);
                  return (
                    <tr key={o.id} style={{ background: checked ? "var(--primary-soft)" : undefined, cursor: "pointer" }} onClick={() => toggle(o.id)}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Izaberi let ${localDay(o.outbound.departure_local)}`}
                          checked={checked}
                          disabled={!checked && selected.length >= maxSelect}
                          onChange={() => toggle(o.id)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </td>
                      <td>
                        <strong>{localDay(o.outbound.departure_local)}</strong> {localTime(o.outbound.departure_local)}
                        <div className="tiny muted">
                          {o.outbound.origin}→{o.outbound.destination}
                          {o.outbound.arrival_local ? ` · dolazak ${localTime(o.outbound.arrival_local)}${o.outbound.arrival_is_estimate ? " (procena)" : ""}` : ""}
                        </div>
                      </td>
                      <td>
                        <strong>{localDay(o.inbound.departure_local)}</strong> {localTime(o.inbound.departure_local)}
                        <div className="tiny muted">
                          {o.inbound.origin}→{o.inbound.destination}
                        </div>
                      </td>
                      <td>
                        {nights + 1} dana / {nights} noći
                      </td>
                      <td className="num">
                        <strong>{money(o.total_fare.amount, "EUR")}</strong>
                        <div className="tiny muted">
                          {money(Number(o.total_fare.amount) / Math.max(people, 1), "EUR")} po osobi
                        </div>
                      </td>
                      <td>
                        <OriginBadge origin={o.evidence.origin} at={o.evidence.retrieved_at} />
                        {o.booking_url ? (
                          <div className="tiny">
                            <a href={o.booking_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                              Wizz Air ↗
                            </a>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      <div className="card compare-tray">
        <strong>
          Izabrano: {selected.length}/{maxSelect}
        </strong>
        <span className="tiny muted">Cene letova su najniže cene po sedištu; prtljag i dodaci se računaju u sledećem koraku.</span>
        <button className="btn primary" style={{ marginLeft: "auto" }} disabled={busy || selected.length === 0} onClick={() => onConfirm(selected)}>
          Traži smeštaj za izabrane letove
        </button>
      </div>
    </section>
  );
}
