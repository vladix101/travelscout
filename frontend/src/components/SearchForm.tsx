import { useState } from "react";
import type { InterpretationNote, Meta, SearchConstraints } from "../types";

const INTERESTS = ["istorija", "muzeji", "arhitektura", "hrana", "priroda", "plaža", "noćni život", "šoping", "umetnost"];
const PACE_LABEL = { relaxed: "Opušteno", moderate: "Umereno", intense: "Intenzivno" } as const;

type Props = {
  meta: Meta;
  value: SearchConstraints;
  onChange: (c: SearchConstraints) => void;
  mode: "demo" | "live";
  onModeChange: (m: "demo" | "live") => void;
  onSearch: (text: string) => void;
  text: string;
  onTextChange: (t: string) => void;
  onInterpret: (text: string) => Promise<void>;
  onSave: (name: string) => Promise<void>;
  interpretNotes: InterpretationNote[] | null;
  busy: boolean;
};

export function validateConstraints(c: SearchConstraints): string[] {
  const errors: string[] = [];
  if (c.origins.length === 0) errors.push("Izaberite bar jedan polazni aerodrom.");
  if (c.min_nights > c.max_nights) errors.push("Minimalan broj noći mora biti ≤ maksimalnog.");
  if (c.date_mode === "exact") {
    if (!c.departure_date || !c.return_date) errors.push("Unesite datum polaska i povratka.");
    else if (c.return_date <= c.departure_date) errors.push("Povratak mora biti posle polaska.");
  } else {
    if (!c.window_start || !c.window_end) errors.push("Unesite period polaska.");
    else if (c.window_end < c.window_start) errors.push("Kraj perioda mora biti posle početka.");
  }
  if (!c.anywhere && c.destinations.length === 0) errors.push("Unesite destinaciju ili izaberite „Bilo gde“.");
  if (c.party.child_ages.length !== c.party.children) errors.push("Unesite uzrast za svako dete.");
  if (c.party.child_ages.some((a) => a < 2 || a > 17)) errors.push("Uzrast deteta mora biti 2–17 (mlađi su bebe).");
  if (c.party.infants > c.party.adults) errors.push("Broj beba ne može biti veći od broja odraslih.");
  if (Number(c.min_review_score) < 7.5) errors.push("Minimalna ocena je 7.5.");
  return errors;
}

function NumberField(props: { label: string; value: number; min: number; max: number; onChange: (n: number) => void; hint?: string }) {
  return (
    <label className="field">
      {props.label}
      <input
        type="number"
        inputMode="numeric"
        min={props.min}
        max={props.max}
        value={props.value}
        onChange={(e) => {
          const n = Math.max(props.min, Math.min(props.max, Number(e.target.value) || props.min));
          props.onChange(n);
        }}
      />
      {props.hint ? <span className="hint">{props.hint}</span> : null}
    </label>
  );
}

export function SearchForm(p: Props) {
  const c = p.value;
  const text = p.text;
  const setText = p.onTextChange;
  const [interpreting, setInterpreting] = useState(false);
  const [destText, setDestText] = useState(c.destinations.join(", "));
  const [touched, setTouched] = useState(false);
  const errors = validateConstraints(c);
  const set = (patch: Partial<SearchConstraints>) => p.onChange({ ...c, ...patch });

  const setChildren = (n: number) => {
    const ages = [...c.party.child_ages];
    while (ages.length < n) ages.push(8);
    set({ party: { ...c.party, children: n, child_ages: ages.slice(0, n) } });
  };

  const toggleOrigin = (iata: string) => {
    const has = c.origins.includes(iata);
    set({ origins: has ? c.origins.filter((o) => o !== iata) : [...c.origins, iata] });
  };

  const toggleInterest = (i: string) =>
    set({ interests: c.interests.includes(i) ? c.interests.filter((x) => x !== i) : [...c.interests, i] });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (errors.length === 0 || text.trim()) p.onSearch(text);
  };

  return (
    <form className="card panel" onSubmit={submit} aria-label="Pretraga putovanja" noValidate>
      <div className="form-section stack">
        <label className="field">
          Opišite putovanje (opciono)
          <textarea
            value={text}
            maxLength={2000}
            placeholder="npr. Vikend u Rimu ili Barseloni za dvoje u novembru, do 300 € po osobi, volimo istoriju i hranu"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <div className="row spread">
          <span className="tiny muted">
            {p.meta.llm_available
              ? "Tekst se automatski primenjuje i pri pretrazi; „Protumači“ samo popunjava formu da je proverite."
              : "Tumačenje teksta zahteva OPENAI_API_KEY."}
          </span>
          <button
            type="button"
            className="btn small"
            disabled={!text.trim() || interpreting || !p.meta.llm_available}
            onClick={async () => {
              setInterpreting(true);
              try {
                await p.onInterpret(text);
              } finally {
                setInterpreting(false);
              }
            }}
          >
            {interpreting ? "Tumačim…" : "Protumači"}
          </button>
        </div>
        {p.interpretNotes && p.interpretNotes.length > 0 ? (
          <div className="notice info small" role="status">
            <strong>Protumačeno – proverite i ispravite polja ispod:</strong>
            <ul>
              {p.interpretNotes.map((n, i) => (
                <li key={i} style={{ color: n.severity === "warning" ? "var(--warn)" : undefined }}>
                  {n.message}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <fieldset className="form-section stack">
        <legend>Polazak</legend>
        <div className="chips" role="group" aria-label="Polazni aerodromi">
          {p.meta.origins.map((o) => (
            <button key={o.iata} type="button" className="chip" aria-pressed={c.origins.includes(o.iata)} title={`${o.city}, ${o.country}`} onClick={() => toggleOrigin(o.iata)}>
              {o.iata} <span style={{ fontWeight: 400 }}>{o.city}</span>
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={c.direct_only} onChange={(e) => set({ direct_only: e.target.checked })} />
          Samo direktni letovi
        </label>
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Datumi</legend>
        <div className="segmented" role="group" aria-label="Režim datuma">
          <button type="button" aria-pressed={c.date_mode === "flexible"} onClick={() => set({ date_mode: "flexible", window_start: c.window_start ?? c.departure_date, window_end: c.window_end ?? c.departure_date })}>
            Fleksibilno
          </button>
          <button type="button" aria-pressed={c.date_mode === "exact"} onClick={() => set({ date_mode: "exact", departure_date: c.departure_date ?? c.window_start, return_date: c.return_date })}>
            Tačni datumi
          </button>
        </div>
        {c.date_mode === "flexible" ? (
          <div className="grid-2">
            <label className="field">
              Polazak od
              <input type="date" value={c.window_start ?? ""} onChange={(e) => set({ window_start: e.target.value || null })} />
            </label>
            <label className="field">
              Polazak do
              <input type="date" value={c.window_end ?? ""} onChange={(e) => set({ window_end: e.target.value || null })} />
            </label>
          </div>
        ) : (
          <div className="grid-2">
            <label className="field">
              Polazak
              <input type="date" value={c.departure_date ?? ""} onChange={(e) => set({ departure_date: e.target.value || null })} />
            </label>
            <label className="field">
              Povratak
              <input type="date" value={c.return_date ?? ""} onChange={(e) => set({ return_date: e.target.value || null })} />
            </label>
          </div>
        )}
        <div className="grid-2">
          <NumberField label="Min. noći" value={c.min_nights} min={1} max={21} onChange={(n) => set({ min_nights: n })} />
          <NumberField label="Maks. noći" value={c.max_nights} min={1} max={21} onChange={(n) => set({ max_nights: n })} />
        </div>
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Destinacija</legend>
        <label className="check">
          <input type="checkbox" checked={c.anywhere} onChange={(e) => set({ anywhere: e.target.checked })} />
          Bilo gde (sve Wizz Air destinacije)
        </label>
        {!c.anywhere ? (
          <label className="field">
            IATA kodovi aerodroma
            <input
              type="text"
              value={destText}
              placeholder="FCO, BCN, BVA"
              onChange={(e) => {
                setDestText(e.target.value);
                const codes = e.target.value
                  .toUpperCase()
                  .split(/[\s,;]+/)
                  .filter((x) => /^[A-Z]{3}$/.test(x));
                set({ destinations: Array.from(new Set(codes)) });
              }}
            />
            <span className="hint">Npr. FCO (Rim), BCN (Barselona), BVA (Pariz), LTN (London), MLA (Malta)</span>
          </label>
        ) : null}
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Putnici i sobe</legend>
        <div className="grid-3">
          <NumberField label="Odrasli" value={c.party.adults} min={1} max={9} onChange={(n) => set({ party: { ...c.party, adults: n } })} />
          <NumberField label="Deca 2–17" value={c.party.children} min={0} max={8} onChange={setChildren} />
          <NumberField label="Bebe 0–1" value={c.party.infants} min={0} max={4} onChange={(n) => set({ party: { ...c.party, infants: n } })} />
        </div>
        {c.party.children > 0 ? (
          <div className="row">
            {c.party.child_ages.map((age, i) => (
              <label key={i} className="field" style={{ width: 90 }}>
                Dete {i + 1}
                <input
                  type="number"
                  min={2}
                  max={17}
                  value={age}
                  onChange={(e) => {
                    const ages = [...c.party.child_ages];
                    ages[i] = Number(e.target.value);
                    set({ party: { ...c.party, child_ages: ages } });
                  }}
                />
              </label>
            ))}
          </div>
        ) : null}
        <NumberField label="Broj soba" value={c.rooms} min={1} max={4} onChange={(n) => set({ rooms: n })} />
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Budžet i prtljag</legend>
        <div className="grid-3">
          <label className="field">
            Budžet
            <input
              type="number"
              min={1}
              placeholder="bez limita"
              value={c.budget_amount ?? ""}
              onChange={(e) => set({ budget_amount: e.target.value ? e.target.value : null })}
            />
          </label>
          <label className="field">
            Važi za
            <select value={c.budget_scope} onChange={(e) => set({ budget_scope: e.target.value as SearchConstraints["budget_scope"] })}>
              <option value="per_person">po osobi</option>
              <option value="group">celu grupu</option>
            </select>
          </label>
          <label className="field">
            Valuta
            <select value={c.budget_currency} onChange={(e) => set({ budget_currency: e.target.value })}>
              <option>EUR</option>
              <option>RSD</option>
            </select>
          </label>
        </div>
        <span className="tiny muted">Budžet se poredi samo sa poznatim troškovima; procene se prikazuju odvojeno.</span>
        <div className="grid-3">
          <NumberField label="Kofer 10 kg" value={c.baggage.cabin_trolley_10kg} min={0} max={9} onChange={(n) => set({ baggage: { ...c.baggage, cabin_trolley_10kg: n } })} />
          <NumberField label="Predati 20 kg" value={c.baggage.checked_bags_20kg} min={0} max={9} onChange={(n) => set({ baggage: { ...c.baggage, checked_bags_20kg: n } })} />
          <NumberField label="Predati 32 kg" value={c.baggage.checked_bags_32kg} min={0} max={9} onChange={(n) => set({ baggage: { ...c.baggage, checked_bags_32kg: n } })} />
        </div>
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Smeštaj</legend>
        <div className="grid-2">
          <label className="field">
            Min. ocena
            <input type="number" step="0.1" min={7.5} max={10} value={c.min_review_score} onChange={(e) => set({ min_review_score: e.target.value || "7.5" })} />
            <span className="hint">Ne može ispod 7.5</span>
          </label>
          <label className="field">
            Maks. km od centra
            <input type="number" step="0.5" min={0.5} placeholder="bez limita" value={c.max_distance_km ?? ""} onChange={(e) => set({ max_distance_km: e.target.value || null })} />
          </label>
        </div>
        <span className="tiny muted">Uvek: bez hostela i spavaonica, potvrđeno privatno kupatilo za izabranu sobu.</span>
      </fieldset>

      <fieldset className="form-section stack">
        <legend>Interesovanja i tempo</legend>
        <div className="chips">
          {INTERESTS.map((i) => (
            <button key={i} type="button" className="chip" aria-pressed={c.interests.includes(i)} onClick={() => toggleInterest(i)}>
              {i}
            </button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Tempo">
          {(Object.keys(PACE_LABEL) as (keyof typeof PACE_LABEL)[]).map((k) => (
            <button key={k} type="button" aria-pressed={c.pace === k} onClick={() => set({ pace: k })}>
              {PACE_LABEL[k]}
            </button>
          ))}
        </div>
      </fieldset>

      <details className="form-section">
        <summary>Rangiranje i prikaz</summary>
        <div className="stack">
          {(
            [
              ["quality_weight", "Kvalitet smeštaja"],
              ["location_weight", "Lokacija"],
              ["time_weight", "Iskoristivo vreme"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="field">
              {label}: {Math.round(c.ranking[k] * 100)}%
              <input type="range" min={0} max={1} step={0.1} value={c.ranking[k]} onChange={(e) => set({ ranking: { ...c.ranking, [k]: Number(e.target.value) } })} />
            </label>
          ))}
          <span className="tiny muted">Cena je uvek primarna; preferencije mogu pomeriti paket najviše ~6% njegove cene po kriterijumu.</span>
          <label className="field">
            Valuta prikaza
            <select value={c.display_currency} onChange={(e) => set({ display_currency: e.target.value as "EUR" | "RSD" })}>
              <option value="EUR">EUR</option>
              <option value="RSD">RSD</option>
            </select>
          </label>
        </div>
      </details>

      <div className="form-section stack">
        <div className="segmented" role="group" aria-label="Izvor podataka">
          <button type="button" aria-pressed={p.mode === "demo"} onClick={() => p.onModeChange("demo")}>
            Demo podaci
          </button>
          <button type="button" aria-pressed={p.mode === "live"} onClick={() => p.onModeChange("live")}>
            Stvarni podaci
          </button>
        </div>
        <span className="tiny muted">
          {p.mode === "demo"
            ? "Izmišljeni podaci za isprobavanje – nisu stvarne ponude."
            : `Wizz Air: ${p.meta.live_flights ? "uključeno" : "isključeno"} · Booking.com: ${p.meta.live_accommodation ? "uključeno" : "isključeno"} · + ručno uneti podaci`}
        </span>
        {touched && errors.length > 0 ? (
          <div className="notice bad small" role="alert">
            <ul>
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <button type="submit" className="btn primary block" disabled={p.busy}>
          {p.busy ? "Pretraga u toku…" : "Pronađi letove"}
        </button>
        <button
          type="button"
          className="btn block"
          onClick={async () => {
            const name = window.prompt("Naziv sačuvane pretrage:", c.anywhere ? "Bilo gde" : c.destinations.join(", "));
            if (name) await p.onSave(name);
          }}
        >
          Sačuvaj pretragu
        </button>
      </div>
    </form>
  );
}
