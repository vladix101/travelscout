import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Favorite, InterpretationNote, Job, Meta, SavedSearch, SearchConstraints, TripPackage } from "./types";
import { SearchForm, validateConstraints } from "./components/SearchForm";
import { ProgressPanel } from "./components/ProgressPanel";
import { Results } from "./components/Results";
import { CompareView } from "./components/CompareView";
import { ItineraryView } from "./components/ItineraryView";
import { IntegrationsPanel } from "./components/IntegrationsPanel";
import { ManualImport } from "./components/ManualImport";
import { FavoritesView, SavedSearchesView } from "./components/Library";

type Tab = "search" | "favorites" | "saved" | "manual" | "integrations";
const TABS: [Tab, string][] = [
  ["search", "Pretraga"],
  ["favorites", "Omiljeno"],
  ["saved", "Sačuvane pretrage"],
  ["manual", "Ručni unos"],
  ["integrations", "Integracije"],
];

const POLL_MS = 1000;
const JOB_KEY = "travelscout:lastJob";

function readLastJob(): string | null {
  try {
    return localStorage.getItem(JOB_KEY);
  } catch {
    return null;
  }
}
function writeLastJob(id: string) {
  try {
    localStorage.setItem(JOB_KEY, id);
  } catch {
    /* storage unavailable */
  }
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("search");
  const [constraints, setConstraints] = useState<SearchConstraints | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [mode, setMode] = useState<"demo" | "live">("demo");
  const [notes, setNotes] = useState<InterpretationNote[] | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [saved, setSaved] = useState<SavedSearch[]>([]);
  const [itinerary, setItinerary] = useState<{ jobId: string; pkg: TripPackage } | null>(null);
  const [comparing, setComparing] = useState<TripPackage[] | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3000);
  };

  const loadLibrary = useCallback(() => {
    api.favorites().then(setFavorites).catch(() => undefined);
    api.savedSearches().then(setSaved).catch(() => undefined);
  }, []);

  const stopPolling = () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const poll = useCallback((jobId: string) => {
    stopPolling();
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        setJob(j);
        if (!["queued", "running"].includes(j.status)) stopPolling();
      } catch (e) {
        setStartError((e as Error).message);
        stopPolling();
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, POLL_MS);
  }, []);

  useEffect(() => {
    api
      .meta()
      .then((m) => {
        setMeta(m);
        setConstraints(m.defaults);
        if (m.live_flights || m.live_accommodation) setMode("live");
      })
      .catch((e: Error) => setMetaError(e.message));
    loadLibrary();
    const last = readLastJob();
    if (last) poll(last);
    return stopPolling;
  }, [loadLibrary, poll]);

  const runSearch = async (c: SearchConstraints, m: "demo" | "live") => {
    const errors = validateConstraints(c);
    if (errors.length) {
      setStartError(errors.join(" "));
      return;
    }
    setStartError(null);
    setTab("search");
    try {
      const { job_id } = await api.startSearch(c, m);
      writeLastJob(job_id);
      setJob(null);
      poll(job_id);
    } catch (e) {
      setStartError((e as Error).message);
    }
  };

  if (metaError) {
    return (
      <main>
        <div className="card empty">
          <h2>Backend nije dostupan</h2>
          <p className="muted">{metaError}</p>
          <p className="small">
            Pokrenite backend: <code>cd backend; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000</code>
          </p>
          <button className="btn primary" onClick={() => window.location.reload()}>
            Pokušaj ponovo
          </button>
        </div>
      </main>
    );
  }

  const running = !!job && ["queued", "running"].includes(job.status);

  return (
    <>
      <header className="app-header">
        <div className="header-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden>
              ✈
            </span>
            <div>
              TravelScout
              <small>Wizz Air + Booking.com + plan obilaska</small>
            </div>
          </div>
          <nav className="tabs" role="tablist" aria-label="Glavni meni">
            {TABS.map(([id, label]) => (
              <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
                {label}
                {id === "favorites" && favorites.length ? <span className="count">{favorites.length}</span> : null}
                {id === "saved" && saved.length ? <span className="count">{saved.length}</span> : null}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main>
        {toast ? (
          <div className="notice info small" role="status" style={{ marginBottom: 12 }}>
            {toast}
          </div>
        ) : null}

        {tab === "search" ? (
          <div className="search-layout">
            <aside>
              {meta && constraints ? (
                <SearchForm
                  key={formKey}
                  meta={meta}
                  value={constraints}
                  onChange={setConstraints}
                  mode={mode}
                  onModeChange={setMode}
                  busy={running}
                  interpretNotes={notes}
                  onSearch={() => runSearch(constraints, mode)}
                  onInterpret={async (text) => {
                    try {
                      const r = await api.interpret(constraints, text);
                      setConstraints(r.constraints);
                      setNotes(r.notes.length ? r.notes : [{ field: "text", message: "Tekst nije promenio nijedno polje.", severity: "info" }]);
                      setFormKey((k) => k + 1);
                    } catch (e) {
                      setNotes([{ field: "text", message: (e as Error).message, severity: "warning" }]);
                    }
                  }}
                  onSave={async (name) => {
                    try {
                      await api.saveSearch(name, constraints);
                      loadLibrary();
                      flash("Pretraga je sačuvana.");
                    } catch (e) {
                      flash(`Čuvanje nije uspelo: ${(e as Error).message}`);
                    }
                  }}
                />
              ) : (
                <div className="card panel">
                  <div className="skeleton" />
                </div>
              )}
            </aside>
            <section className="stack" aria-label="Rezultati">
              {startError ? <div className="notice bad">{startError}</div> : null}
              {job ? (
                <ProgressPanel
                  job={job}
                  onCancel={async () => {
                    await api.cancel(job.id).catch(() => undefined);
                  }}
                />
              ) : null}
              {job?.result ? (
                <Results
                  key={job.id}
                  result={job.result}
                  jobId={job.id}
                  onItinerary={(pkg) => setItinerary({ jobId: job.id, pkg })}
                  onFavorite={async (pkg) => {
                    try {
                      await api.addFavorite(job.id, pkg);
                      loadLibrary();
                      flash(`${pkg.destination_name} je dodat u omiljeno.`);
                    } catch (e) {
                      flash((e as Error).message);
                    }
                  }}
                  onCompare={setComparing}
                />
              ) : null}
              {!job && !startError ? (
                <div className="card empty">
                  <h2>Pronađite najjeftinije realno putovanje</h2>
                  <p className="muted">
                    Unesite parametre levo ili opišite putovanje rečima. Agenti traže Wizz Air letove, Booking.com smeštaj sa ocenom ≥ 7.5 i
                    potvrđenim privatnim kupatilom, računaju ukupnu cenu i prave plan obilaska.
                  </p>
                  <p className="small muted">TravelScout samo preporučuje i šalje vas na sajt provajdera – ne rezerviše i ne naplaćuje.</p>
                </div>
              ) : null}
              {running && !job?.result ? (
                <div className="card panel stack">
                  <div className="skeleton" />
                  <div className="skeleton" />
                </div>
              ) : null}
            </section>
          </div>
        ) : null}

        {tab === "favorites" ? (
          <FavoritesView
            items={favorites}
            onRemove={async (id) => {
              await api.deleteFavorite(id);
              loadLibrary();
            }}
            onItinerary={(jobId, pkg) => setItinerary({ jobId, pkg })}
          />
        ) : null}

        {tab === "saved" ? (
          <SavedSearchesView
            items={saved}
            onLoad={(s) => {
              setConstraints(s.constraints);
              setFormKey((k) => k + 1);
              setNotes(null);
              setTab("search");
            }}
            onRun={(s) => {
              setConstraints(s.constraints);
              setFormKey((k) => k + 1);
              void runSearch(s.constraints, mode);
            }}
            onRemove={async (id) => {
              await api.deleteSavedSearch(id);
              loadLibrary();
            }}
          />
        ) : null}

        {tab === "manual" && meta ? <ManualImport meta={meta} onChanged={() => undefined} /> : null}
        {tab === "integrations" ? <IntegrationsPanel /> : null}
      </main>

      <footer className="app-footer">
        TravelScout daje preporuke i preusmerava na Wizz Air i Booking.com; ne vrši rezervacije niti prima podatke o plaćanju. Nije povezan sa
        Wizz Air-om ni Booking.com-om.
      </footer>

      {itinerary ? <ItineraryView jobId={itinerary.jobId} pkg={itinerary.pkg} onClose={() => setItinerary(null)} /> : null}
      {comparing ? <CompareView packages={comparing} onClose={() => setComparing(null)} /> : null}
    </>
  );
}
