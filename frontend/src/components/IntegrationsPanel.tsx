import { useEffect, useState } from "react";
import { api } from "../api";
import type { Integrations } from "../types";
import { timestamp } from "../format";

const STATUS: Record<string, [string, string]> = {
  active: ["ok", "Aktivno"],
  disabled: ["neutral", "Isključeno"],
  missing: ["warn", "Nedostaje ključ"],
  unavailable: ["bad", "Nije dostupno"],
  error: ["bad", "Greška"],
};

export function IntegrationsPanel() {
  const [data, setData] = useState<Integrations | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.integrations().then(setData).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="stack">
      <section className="card panel stack">
        <h2>Status integracija</h2>
        <div className="small">
          <p style={{ marginTop: 0 }}>
            <strong>Samo sa OpenAI ključem:</strong> agenti (tumačenje zahteva, orkestracija pretrage letova, objašnjenja i planovi putovanja),
            demo pretraga celog toka, ručni uvoz, linkovi ka provajderima i planovi na osnovu OpenStreetMap podataka.
          </p>
          <p>
            <strong>Uz uključene opcije u .env:</strong> stvarne Wizz Air cene iz javnog kalendara cena (<code>WIZZ_LIVE_ENABLED=true</code>) i stvarni
            Booking.com smeštaj preko vidljivog pregledača (<code>BOOKING_SCRAPER_ENABLED=true</code>). Ovo su nezvanični izvori za ličnu upotrebu i mogu
            prestati da rade bez najave.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>Nedostupno:</strong> zvanični Wizz Air API (ne postoji), Booking.com Demand API (zahteva partnerski ugovor), brojevi letova, tačna
            vremena sletanja i cene prtljaga Wizz Air-a (zaštićeno od botova – ne zaobilazimo zaštitu).
          </p>
        </div>
        {data && !data.env_file_found ? <div className="notice warn small">Fajl .env nije pronađen u korenu projekta.</div> : null}
      </section>
      {error ? <div className="notice bad">{error}</div> : null}
      {!data && !error ? <div className="skeleton" /> : null}
      {data ? (
        <>
          <div className="integrations">
            {data.items.map((it) => {
              const [tone, label] = STATUS[it.status] ?? ["neutral", it.status];
              return (
                <article key={it.id} className="card integration">
                  <div className="row spread">
                    <h3>{it.name}</h3>
                    <span className={`badge ${tone}`}>{label}</span>
                  </div>
                  <p className="small" style={{ margin: 0 }}>
                    {it.detail}
                  </p>
                  <span className="tiny muted">Oznaka podataka: {it.data}</span>
                </article>
              );
            })}
          </div>
          <section className="card panel stack">
            <h3>Poslednja pretraga</h3>
            {data.last_run.length === 0 ? <p className="small muted">Još nema pretraga u ovoj sesiji servera.</p> : null}
            {data.last_run.map((s) => (
              <div key={s.provider} className="row small">
                <span className={`badge ${s.status === "ok" ? "ok" : s.status === "disabled" ? "neutral" : "bad"}`}>{s.status}</span>
                <strong>{s.provider}</strong>
                <span className="muted">{s.message}</span>
                <span className="tiny muted">{timestamp(s.checked_at)}</span>
              </div>
            ))}
            <span className="tiny muted">Provereno: {timestamp(data.checked_at)}</span>
          </section>
        </>
      ) : null}
    </div>
  );
}
