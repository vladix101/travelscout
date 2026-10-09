import { useEffect, useState } from "react";
import { api } from "../api";
import type { Itinerary, TripPackage } from "../types";
import { localDay, money } from "../format";
import { Modal } from "./Modal";

const KIND_LABEL: Record<string, string> = {
  sight: "",
  meal: "🍽",
  transfer: "🚌",
  break: "☕",
  free_time: "🚶",
  checkin: "🏨",
  checkout: "🧳",
};

export function ItineraryView({ jobId, pkg, onClose }: { jobId: string; pkg: TripPackage; onClose: () => void }) {
  const [data, setData] = useState<Itinerary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .itinerary(jobId, pkg.id)
      .then((it) => alive && setData(it))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [jobId, pkg.id]);

  const actions = data ? (
    <>
      <a className="btn small" href={api.exportUrl(jobId, pkg.id, "md")} download>
        Markdown
      </a>
      <a className="btn small" href={api.exportUrl(jobId, pkg.id, "json")} download>
        JSON
      </a>
    </>
  ) : null;

  return (
    <Modal title={`Plan putovanja – ${pkg.destination_name}`} onClose={onClose} actions={actions}>
      {!data && !error ? (
        <div className="stack" aria-busy="true">
          <p className="muted small">Itinerary Agent prikuplja znamenitosti (OpenStreetMap) i pravi plan…</p>
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : null}
      {error ? <div className="notice bad">Plan nije napravljen: {error}</div> : null}
      {data ? (
        <div className="stack">
          <div className="row" style={{ gap: 6 }}>
            <span className={`badge ${data.used_llm ? "ok" : "neutral"}`}>{data.used_llm ? "Itinerary Agent (OpenAI)" : "Plan po pravilima"}</span>
            {data.sources.map((s) => (
              <span key={s} className="badge neutral">
                {s}
              </span>
            ))}
          </div>
          <div className="notice warn small">
            <ul style={{ marginTop: 0 }}>
              {data.disclaimers.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          </div>
          <section className="stack">
            <h3>Transfer aerodrom ↔ smeštaj</h3>
            {data.transfers.map((t, i) => (
              <div key={i} className="small">
                <strong>{t.mode}</strong>: {t.description}
                {t.est_duration_min ? ` · ~${t.est_duration_min} min` : ""}
                {t.est_cost_eur ? ` · ~${money(t.est_cost_eur, "EUR")}` : ""} {!t.verified ? <span className="badge warn">procena</span> : null}{" "}
                {t.source_url ? (
                  <a href={t.source_url} target="_blank" rel="noopener noreferrer">
                    ruta na mapi ↗
                  </a>
                ) : null}
              </div>
            ))}
          </section>
          {data.days.length === 0 ? <div className="notice warn">Plan nema dana u okviru putovanja.</div> : null}
          {data.days.map((d) => (
            <section key={d.date} className="day">
              <div className="row spread">
                <h3>
                  {localDay(d.date)} – {d.title}
                </h3>
                <span className="tiny muted">
                  procena: znamenitosti ~{money(d.est_sightseeing_eur, "EUR")}, prevoz ~{money(d.est_transport_eur, "EUR")}
                </span>
              </div>
              {d.area ? <div className="small muted">Kvart: {d.area}</div> : null}
              <ul className="timeline">
                {d.items.map((it, i) => (
                  <li key={i} className={`kind-${it.kind}`}>
                    <span className="when">
                      {it.start}–{it.end}
                    </span>
                    <span className="what">
                      {KIND_LABEL[it.kind]} <strong>{it.title}</strong>
                      {it.kind === "sight" && it.paid === true ? <span className="badge neutral" style={{ marginLeft: 6 }}>plaća se</span> : null}
                      {it.kind === "sight" && it.paid === false ? <span className="badge ok" style={{ marginLeft: 6 }}>besplatno</span> : null}
                      {it.kind === "sight" && !it.verified ? <span className="badge warn" style={{ marginLeft: 6 }}>neprovereno</span> : null}
                      {it.description ? <div className="small muted">{it.description}</div> : null}
                      <div className="tiny">
                        {it.opening_hours ? <span className="muted">Radno vreme (OSM, proveriti): {it.opening_hours} · </span> : null}
                        {it.map_url ? (
                          <a href={it.map_url} target="_blank" rel="noopener noreferrer">
                            mapa ↗
                          </a>
                        ) : null}
                        {it.source_url ? (
                          <>
                            {" · "}
                            <a href={it.source_url} target="_blank" rel="noopener noreferrer">
                              zvanične informacije ↗
                            </a>
                          </>
                        ) : null}
                      </div>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : null}
    </Modal>
  );
}
