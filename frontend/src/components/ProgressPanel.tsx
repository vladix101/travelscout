import type { Job } from "../types";

const ACCOMMODATION_STAGES = new Set(["accommodation", "eligibility", "totals", "rank"]);

function currentStep(job: Job): 1 | 2 | 3 {
  if (job.status === "awaiting_selection") return 1;
  if (["done", "partial"].includes(job.status) && (job.result?.packages.length ?? 0) > 0) return 3;
  // Flights were already chosen (phase 2 of the search) or accommodation work has started.
  if ((job.result?.flight_choices.length ?? 0) > 0) return 2;
  return job.progress.some((e) => ACCOMMODATION_STAGES.has(e.stage) || e.stage === "select") ? 2 : 1;
}

function headline(job: Job, step: number): string {
  if (job.status === "failed") return "Pretraga nije uspela";
  if (job.status === "cancelled") return "Pretraga je otkazana";
  if (job.status === "awaiting_selection") return "Letovi su pronađeni – izaberite datume";
  if (job.status === "done") return "Gotovo";
  if (job.status === "partial") return "Gotovo, uz delimične rezultate";
  return step === 1 ? "Tražim letove za vaše datume…" : "Tražim smeštaj za izabrane letove…";
}

export function ProgressPanel({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const step = currentStep(job);
  const running = ["queued", "running"].includes(job.status);
  const steps: [number, string][] = [
    [1, "Letovi"],
    [2, "Smeštaj"],
    [3, "Putovanje"],
  ];
  return (
    <section className="card panel" aria-live="polite" aria-busy={running} style={{ paddingTop: 14 }}>
      <div className="stepper">
        {steps.map(([n, label]) => (
          <div key={n} className={`step ${n === step ? "active" : n < step ? "done" : ""}`}>
            <span className="n">{n}</span>
            {label}
          </div>
        ))}
      </div>
      <div className="status-line">
        {running ? <span className="spinner" aria-hidden /> : null}
        <span>{headline(job, step)}</span>
        {job.mode === "demo" ? <span className="badge demo">Demo podaci</span> : null}
        {running ? (
          <button className="btn small ghost danger" style={{ marginLeft: "auto" }} onClick={onCancel}>
            Otkaži
          </button>
        ) : null}
      </div>
      {job.error ? (
        <div className={`notice ${job.status === "failed" ? "bad" : "warn"} small`} style={{ marginTop: 10 }}>
          {job.error}
        </div>
      ) : null}
    </section>
  );
}
