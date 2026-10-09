import { useMemo, useState } from "react";
import type { FlightOption, SearchResult } from "../types";
import { localDay, localTime, money, nightsBetween } from "../format";

type Props = {
  result: SearchResult;
  maxSelect: number;
  busy: boolean;
  onConfirm: (ids: string[]) => void;
};

function defaultPick(choices: FlightOption[], max: number): string[] {
  // Cheapest option per destination first, then the next cheapest (other dates), up to `max`.
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
  const people = Math.max(result.constraints.party.adults + result.constraints.party.children, 1);
  const cheapest = Math.min(...choices.map((o) => Number(o.total_fare.amount)));

  const groups = useMemo(() => {
    const map = new Map<string, FlightOption[]>();
    for (const o of choices) map.set(o.outbound.destination, [...(map.get(o.outbound.destination) ?? []), o]);
    return Array.from(map.entries())
      .map(([dest, opts]) => ({
        dest,
        min: Math.min(...opts.map((o) => Number(o.total_fare.amount))),
        list: [...opts].sort((a, b) =>
          sort === "price"
            ? Number(a.total_fare.amount) - Number(b.total_fare.amount)
            : a.outbound.departure_local.localeCompare(b.outbound.departure_local) ||
              a.inbound.departure_local.localeCompare(b.inbound.departure_local),
        ),
      }))
      .sort((a, b) => a.min - b.min);
  }, [choices, sort]);

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= maxSelect ? s : [...s, id]));

  if (choices.length === 0) {
    return (
      <div className="card empty">
        <h2>Nema letova za ove uslove</h2>
        <p className="muted">Proširite period polaska ili raspon noći, ili dodajte još polaznih aerodroma.</p>
        {result.suggestions.length ? (
          <ul className="small">
            {result.suggestions.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  }

  return (
    <section className="card panel stack" aria-label="Izbor letova">
      <div className="row spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow">Korak 1</div>
          <h2>Izaberite letove</h2>
          <p className="small muted" style={{ margin: "4px 0 0" }}>
            {choices.length} {choices.length === 1 ? "kombinacija" : "kombinacija"} datuma. Smeštaj tražimo tačno za noći izabranih letova.
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Sortiranje">
          <button type="button" aria-pressed={sort === "date"} onClick={() => setSort("date")}>
            Po datumu
          </button>
          <button type="button" aria-pressed={sort === "price"} onClick={() => setSort("price")}>
            Po ceni
          </button>
        </div>
      </div>
      {result.mode === "demo" ? <div className="notice demo small">Demo letovi – nisu stvarne ponude.</div> : null}

      {groups.map(({ dest, list }) => (
        <div key={dest} className="choice-group">
          <h3>
            {result.destination_cities[dest] ?? dest} <span className="muted" style={{ fontSize: "0.9rem" }}>{dest}</span>
          </h3>
          {list.map((o) => {
            const nights = nightsBetween(o.outbound.departure_local, o.inbound.departure_local);
            const checked = selected.includes(o.id);
            const isCheapest = Number(o.total_fare.amount) === cheapest;
            return (
              <div
                key={o.id}
                className={`choice ${checked ? "selected" : ""}`}
                onClick={() => toggle(o.id)}
                role="checkbox"
                aria-checked={checked}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === " " || e.key === "Enter") {
                    e.preventDefault();
                    toggle(o.id);
                  }
                }}
              >
                <input type="checkbox" tabIndex={-1} aria-hidden checked={checked} disabled={!checked && selected.length >= maxSelect} readOnly />
                <div>
                  <div className="leg-date">Polazak · {localDay(o.outbound.departure_local)}</div>
                  <div className="leg-time">
                    {localTime(o.outbound.departure_local)} <span className="tiny muted">{o.outbound.origin}→{o.outbound.destination}</span>
                  </div>
                </div>
                <div>
                  <div className="leg-date">Povratak · {localDay(o.inbound.departure_local)}</div>
                  <div className="leg-time">
                    {localTime(o.inbound.departure_local)} <span className="tiny muted">{o.inbound.origin}→{o.inbound.destination}</span>
                  </div>
                </div>
                <div className="dur">
                  {nights} {nights === 1 ? "noć" : "noći"}
                  <div className="tiny muted">{nights + 1} dana</div>
                </div>
                <div className="price">
                  <b className="tnum">{money(o.total_fare.amount, "€")}</b>
                  <div className="tiny muted">
                    {money(Number(o.total_fare.amount) / people, "€")} po osobi{isCheapest ? " · najjeftinije" : ""}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ))}

      <div className="sticky-bar">
        <span>
          <strong>{selected.length}</strong> od {maxSelect} izabrano
        </span>
        <span className="tiny muted">Cene su najniže cene po sedištu; prtljag i takse dolaze u sledećem koraku.</span>
        <button className="btn primary" style={{ marginLeft: "auto" }} disabled={busy || selected.length === 0} onClick={() => onConfirm(selected)}>
          {busy ? "Šaljem…" : "Traži smeštaj za izabrane"}
        </button>
      </div>
    </section>
  );
}
