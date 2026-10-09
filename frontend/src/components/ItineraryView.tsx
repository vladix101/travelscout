import { useEffect, useState } from "react";
import { api } from "../api";
import type { Itinerary, TripPackage } from "../types";
import { localDay, money } from "../format";
import { Modal } from "./Modal";

const KIND_LABEL: Record<string, string> = {
  sight: "",
  meal: "obrok",
  transfer: "prevoz",
  break: "pauza",
  free_time: "slobodno",
  checkin: "smeštaj",
  checkout: "odjava",
};

/** Reusable itinerary rendering (modal and saved-trip detail). */
export function ItineraryBody({ data }: { data: Itinerary }) {
  return (
    <div className="stack" style={{ gap: 0 }}>
      <div className="day" style={{ borderTop: 0, paddingTop: 0 }}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>
          Aerodrom ↔ smeštaj
        </div>
        {data.transfers.map((t, i) => (
          <div key={i} className="small" style={{ marginBottom: 4 }}>
            <strong>{t.mode}</strong> — {t.description}
            {t.est_duration_min ? ` · ~${t.est_duration_min} min` : ""}
            {t.est_cost_eur ? ` · ~${money(t.est_cost_eur, "EUR")}` : ""}
            {!t.verified ? <span className="muted"> (procena)</span> : null}{" "}
            {t.source_url ? (
              <a href={t.source_url} target="_blank" rel="noopener noreferrer">
                ruta ↗
              </a>
            ) : null}
          </div>
        ))}
      </div>
      {data.days.length === 0 ? <div className="notice warn">Plan nema dana u okviru putovanja.</div> : null}
      {data.days.map((d, idx) => (
        <section key={d.date} className="day">
          <div className="day-head">
            <h3>
              <span className="muted">Dan {idx + 1} ·</span>{" "}
              {localDay(d.date)} — {d.title}
            </h3>
            <span className="tiny muted">
              ~{money(d.est_sightseeing_eur, "€")} ulaznice · ~{money(d.est_transport_eur, "€")} prevoz
            </span>
          </div>
          {d.area ? <div className="small muted">{d.area}</div> : null}
          <ul className="timeline">
            {d.items.map((it, i) => (
              <li key={i} className={`kind-${it.kind}`}>
                <span className="when">
                  {it.start}–{it.end}
                </span>
                <span className="what">
                  {KIND_LABEL[it.kind] ? <span className="kind">{KIND_LABEL[it.kind]}</span> : null}
                  {it.kind === "sight" ? <strong>{it.title}</strong> : it.title}
                  {it.kind === "sight" && it.paid === false ? <span className="muted"> · besplatno</span> : null}
                  {it.kind === "sight" && it.paid === true ? <span className="muted"> · ulaznica</span> : null}
                  {it.kind === "sight" && !it.verified ? <span style={{ color: "var(--warn)" }}> · neprovereno</span> : null}
                  {it.description ? <div className="small muted">{it.description}</div> : null}
                  {it.opening_hours || it.map_url || it.source_url ? (
                    <div className="tiny">
                      {it.opening_hours ? <span className="muted">Radno vreme (OSM, proveriti): {it.opening_hours} · </span> : null}
                      {it.map_url ? (
                        <a href={it.map_url} target="_blank" rel="noopener noreferrer">
                          mapa
                        </a>
                      ) : null}
                      {it.source_url ? (
                        <>
                          {" · "}
                          <a href={it.source_url} target="_blank" rel="noopener noreferrer">
                            više informacija
                          </a>
                        </>
                      ) : null}
                    </div>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <ul className="tiny muted" style={{ margin: "12px 0 0", paddingLeft: 18 }}>
        {data.disclaimers.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </div>
  );
}

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

  return (
    <Modal title={`Plan obilaska · ${pkg.destination_name}`} onClose={onClose}>
      {!data && !error ? (
        <div className="stack" aria-busy="true">
          <p className="muted small">Pravim plan na osnovu znamenitosti iz OpenStreetMap-a i vremena vaših letova…</p>
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : null}
      {error ? <div className="notice bad">Plan nije napravljen: {error}</div> : null}
      {data ? <ItineraryBody data={data} /> : null}
    </Modal>
  );
}
