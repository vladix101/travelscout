import type { Job } from "../types";
import { timestamp } from "../format";

const STAGES: [string, string][] = [
  ["validate", "Provera"],
  ["flights", "Letovi"],
  ["shortlist", "Uži izbor"],
  ["accommodation", "Smeštaj"],
  ["eligibility", "Uslovi"],
  ["totals", "Troškovi"],
  ["rank", "Rangiranje"],
];

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "U redu čekanja",
  running: "U toku",
  done: "Završeno",
  partial: "Delimični rezultati",
  failed: "Greška",
  cancelled: "Otkazano",
};

export function ProgressPanel({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const seen = new Set(job.progress.map((e) => e.stage));
  const finished = !["queued", "running"].includes(job.status);
  const doneStages = new Set(job.progress.filter((e) => e.status === "done" || finished).map((e) => e.stage));
  const idx = STAGES.reduce((acc, [s], i) => (seen.has(s) ? i + 1 : acc), 0);
  const pct = finished ? 100 : Math.max(5, Math.round((idx / STAGES.length) * 100));
  const events = [...job.progress].reverse().slice(0, 40);
  const tone = job.status === "failed" ? "bad" : job.status === "partial" || job.status === "cancelled" ? "warn" : finished ? "ok" : "neutral";

  return (
    <section className="card panel stack" aria-live="polite" aria-busy={!finished}>
      <div className="row spread">
        <div className="row">
          <h2>Napredak agenata</h2>
          <span className={`badge ${tone}`}>{STATUS_LABEL[job.status]}</span>
          {job.mode === "demo" ? <span className="badge demo">DEMO</span> : null}
        </div>
        {!finished ? (
          <button className="btn small danger" onClick={onCancel}>
            Otkaži
          </button>
        ) : null}
      </div>
      <div className="bar" aria-hidden>
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="stages">
        {STAGES.map(([s, label]) => (
          <span key={s} className={`stage ${doneStages.has(s) ? "done" : seen.has(s) ? "active" : ""}`}>
            {label}
          </span>
        ))}
      </div>
      {job.error ? <div className={`notice ${job.status === "failed" ? "bad" : "warn"} small`}>{job.error}</div> : null}
      <details open={!finished}>
        <summary>Dnevnik koraka ({job.progress.length})</summary>
        <ul className="progress-list">
          {events.map((e, i) => (
            <li key={i}>
              <span className={`dot ${e.status}`} aria-hidden />
              <span>
                {e.agent ? <strong>{e.agent}: </strong> : null}
                {e.message}
                {e.tool ? <span className="badge neutral" style={{ marginLeft: 6 }}>alat: {e.tool}</span> : null}
              </span>
              <span className="tiny muted">{timestamp(e.at)}</span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
