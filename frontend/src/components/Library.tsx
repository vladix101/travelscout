import type { SavedSearch } from "../types";
import { localDate, timestamp } from "../format";

export function SavedSearchesView({ items, onLoad, onRun, onRemove }: {
  items: SavedSearch[];
  onLoad: (s: SavedSearch) => void;
  onRun: (s: SavedSearch) => void;
  onRemove: (id: string) => void;
}) {
  if (items.length === 0) {
    return (
      <div className="card empty">
        <h2>Nema sačuvanih pretraga</h2>
        <p className="muted">Na formi za pretragu kliknite „Sačuvaj pretragu“.</p>
      </div>
    );
  }
  return (
    <div className="stack">
      {items.map((s) => {
        const c = s.constraints;
        const dates = c.date_mode === "exact" ? `${c.departure_date ? localDate(c.departure_date) : "?"} – ${c.return_date ? localDate(c.return_date) : "?"}` : `polazak ${c.window_start ? localDate(c.window_start) : "?"} – ${c.window_end ? localDate(c.window_end) : "?"}`;
        return (
          <article key={s.id} className="card panel row spread">
            <div>
              <h3>{s.name}</h3>
              <div className="small muted">
                {c.origins.join(", ")} → {c.anywhere ? "bilo gde" : c.destinations.join(", ")} · {dates} · {c.min_nights}–{c.max_nights} noći ·{" "}
                {c.party.adults + c.party.children + c.party.infants} putnika
              </div>
              <div className="tiny muted">Sačuvano {timestamp(s.created_at)}</div>
            </div>
            <div className="row">
              <button className="btn small" onClick={() => onLoad(s)}>
                Učitaj u formu
              </button>
              <button className="btn small primary" onClick={() => onRun(s)}>
                Pokreni
              </button>
              <button className="btn small danger" onClick={() => onRemove(s.id)}>
                Obriši
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}
