import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { InterpretationNote, Job, MailStatus, Meta, SavedSearch, SavedTrip, SearchConstraints, TripPackage } from "./types";
import { SearchForm, validateConstraints } from "./components/SearchForm";
import { ProgressPanel } from "./components/ProgressPanel";
import { Results } from "./components/Results";
import { CompareView } from "./components/CompareView";
import { ItineraryView } from "./components/ItineraryView";
import { IntegrationsPanel } from "./components/IntegrationsPanel";
import { ManualImport } from "./components/ManualImport";
import { SavedSearchesView } from "./components/Library";
import { FlightChoices } from "./components/FlightChoices";
import { MyTrips } from "./components/MyTrips";

type Tab = "search" | "trips" | "saved" | "manual" | "integrations";
const TABS: [Tab, string][] = [
  ["search", "Pretraga"],
  ["trips", "Moja putovanja"],
  ["saved", "Sačuvani filteri"],
  ["manual", "Ručni unos"],
  ["integrations", "Izvori podataka"],
];

const POLL_MS = 1000;
const JOB_KEY = "travelscout:lastJob";
const MODE_KEY = "travelscout:mode";

function readLastJob(): string | null {
  try {
    return localStorage.getItem(JOB_KEY);
  } catch {
    return null;
  }
}
function readMode(): "demo" | "live" | null {
  try {
    const m = localStorage.getItem(MODE_KEY);
    return m === "demo" || m === "live" ? m : null;
  } catch {
    return null;
  }
}
function writeMode(m: "demo" | "live") {
  try {
    localStorage.setItem(MODE_KEY, m);
  } catch {
    /* storage unavailable */
  }
}
function writeLastJob(id: string) {
  try {
    localStorage.setItem(JOB_KEY, id);
  } catch {
    /* storage unavailable */
  }
}

type SaveState = "idle" | "saving" | "saved";

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("search");
  const [constraints, setConstraints] = useState<SearchConstraints | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [mode, setModeState] = useState<"demo" | "live">("demo");
  const [mail, setMail] = useState<MailStatus | null>(null);
  const setMode = (m: "demo" | "live") => {
    setModeState(m);
    writeMode(m);
  };
  const [notes, setNotes] = useState<InterpretationNote[] | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [trips, setTrips] = useState<SavedTrip[]>([]);
  const [openTrip, setOpenTrip] = useState<string | null>(null);
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({});
  const [saved, setSaved] = useState<SavedSearch[]>([]);
  const [itinerary, setItinerary] = useState<{ jobId: string; pkg: TripPackage } | null>(null);
  const [comparing, setComparing] = useState<TripPackage[] | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [interpretedText, setInterpretedText] = useState("");
  const [nlText, setNlText] = useState("");
  const [selecting, setSelecting] = useState(false);
  const pollRef = useRef<number | null>(null);

  const flash = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3500);
  };

  const loadLibrary = useCallback(() => {
    api.trips().then(setTrips).catch(() => undefined);
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
        if (j.status !== "awaiting_selection") setSelecting(false);
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
        setModeState(readMode() ?? (m.live_flights || m.live_accommodation ? "live" : "demo"));
      })
      .catch((e: Error) => setMetaError(e.message));
    loadLibrary();
    api.mailStatus().then(setMail).catch(() => setMail(null));
    const last = readLastJob();
    if (last) poll(last);
    return stopPolling;
  }, [loadLibrary, poll]);

  const interpret = async (base: SearchConstraints, text: string): Promise<SearchConstraints> => {
    const r = await api.interpret(base, text);
    setConstraints(r.constraints);
    setNotes(r.notes.length ? r.notes : [{ field: "text", message: "Opis nije promenio nijedan filter.", severity: "info" }]);
    setFormKey((k) => k + 1);
    setInterpretedText(text.trim());
    return r.constraints;
  };

  const runSearch = async (base: SearchConstraints, m: "demo" | "live", text = "") => {
    let c = base;
    // The written request wins over stale filter values: apply it before searching.
    if (text.trim() && meta?.llm_available && text.trim() !== interpretedText) {
      try {
        c = await interpret(base, text);
      } catch (e) {
        setStartError(`Opis nije mogao da se primeni: ${(e as Error).message}`);
        return;
      }
    }
    const errors = validateConstraints(c);
    if (errors.length) {
      setStartError(errors.join(" "));
      return;
    }
    setStartError(null);
    setTab("search");
    setSaveStates({});
    try {
      const { job_id } = await api.startSearch(c, m);
      writeLastJob(job_id);
      setJob(null);
      poll(job_id);
    } catch (e) {
      setStartError((e as Error).message);
    }
  };

  const saveTrip = async (pkg: TripPackage) => {
    if (!job) return;
    setSaveStates((s) => ({ ...s, [pkg.id]: "saving" }));
    try {
      const trip = await api.saveTrip(job.id, pkg.id);
      setSaveStates((s) => ({ ...s, [pkg.id]: "saved" }));
      loadLibrary();
      flash(`${trip.title} je u „Moja putovanja“.`);
    } catch (e) {
      setSaveStates((s) => ({ ...s, [pkg.id]: "idle" }));
      flash(`Čuvanje nije uspelo: ${(e as Error).message}`);
    }
  };

  if (metaError) {
    return (
      <main>
        <div className="card empty">
          <h2>Server nije pokrenut</h2>
          <p className="muted">{metaError}</p>
          <p className="small">
            <code>cd backend; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000</code>
          </p>
          <button className="btn primary" onClick={() => window.location.reload()}>
            Pokušaj ponovo
          </button>
        </div>
      </main>
    );
  }

  const running = !!job && ["queued", "running"].includes(job.status);
  const awaiting = job?.status === "awaiting_selection";

  return (
    <>
      <header className="app-header">
        <div className="header-inner">
          <div className="brand">
            <span className="brand-name">TravelScout</span>
            <span className="brand-tag">najjeftinija realna putovanja</span>
          </div>
          <nav className="tabs" role="tablist" aria-label="Glavni meni">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                role="tab"
                className="tab"
                aria-selected={tab === id}
                onClick={() => {
                  setTab(id);
                  if (id === "trips") setOpenTrip(null);
                }}
              >
                {label}
                {id === "trips" && trips.length ? <span className="count">{trips.length}</span> : null}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main>
        {tab === "search" ? (
          <div className="search-layout">
            <aside>
              {meta && constraints ? (
                <SearchForm
                  key={formKey}
                  text={nlText}
                  onTextChange={setNlText}
                  meta={meta}
                  value={constraints}
                  onChange={setConstraints}
                  mode={mode}
                  busy={running}
                  interpretNotes={notes}
                  onSearch={(text) => runSearch(constraints, mode, text)}
                  onInterpret={async (text) => {
                    try {
                      await interpret(constraints, text);
                    } catch (e) {
                      setNotes([{ field: "text", message: (e as Error).message, severity: "warning" }]);
                    }
                  }}
                  onSave={async (name) => {
                    try {
                      await api.saveSearch(name, constraints);
                      loadLibrary();
                      flash("Filteri su sačuvani.");
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
            <section className="stack" style={{ gap: 18 }} aria-label="Rezultati">
              {startError ? <div className="notice bad">{startError}</div> : null}
              {job ? <ProgressPanel job={job} onCancel={() => void api.cancel(job.id).catch(() => undefined)} /> : null}
              {awaiting && !selecting && job?.result && meta ? (
                <FlightChoices
                  key={job.id}
                  result={job.result}
                  maxSelect={meta.max_shortlist}
                  busy={selecting}
                  onConfirm={async (ids) => {
                    setSelecting(true);
                    try {
                      await api.selectFlights(job.id, ids);
                      poll(job.id);
                    } catch (e) {
                      setStartError((e as Error).message);
                      setSelecting(false);
                    }
                  }}
                />
              ) : null}
              {running || (selecting && awaiting) ? (
                <section className="card panel stack" aria-busy="true">
                  <p className="small muted" style={{ margin: 0 }}>
                    {job?.result?.flight_choices.length
                      ? "Proveravam sobe, ocene, kupatila i cene za tačne noći vaših letova. Obično traje do minut."
                      : "Tražim sve kombinacije datuma u vašem periodu."}
                  </p>
                  <div className="skeleton" />
                  <div className="skeleton" />
                </section>
              ) : null}
              {job?.result && !awaiting && !running ? (
                <Results
                  key={job.id}
                  result={job.result}
                  jobId={job.id}
                  onItinerary={(pkg) => setItinerary({ jobId: job.id, pkg })}
                  onSave={saveTrip}
                  saveStates={saveStates}
                  onCompare={setComparing}
                  mail={mail}
                  onSendMail={(pkg, to) => api.emailPackage(job.id, pkg.id, to)}
                />
              ) : null}
              {!job && !startError ? (
                <div className="card empty">
                  <div className="eyebrow">Kako radi</div>
                  <h2>Napišite kuda želite, mi tražimo najjeftiniju realnu kombinaciju.</h2>
                  <p className="muted">
                    Ne samo najjeftiniji let: ukupna cena leta i smeštaja sa ocenom ≥ 7.5 i privatnim kupatilom, plus plan obilaska po danima.
                  </p>
                  <div className="how">
                    <div>
                      <div className="n">1</div>
                      <strong>Letovi</strong>
                      <p className="small muted">Sve Wizz Air kombinacije datuma u vašem periodu – birate koje vam odgovaraju.</p>
                    </div>
                    <div>
                      <div className="n">2</div>
                      <strong>Smeštaj</strong>
                      <p className="small muted">Booking.com za tačne noći izabranih letova, samo ponude koje ispunjavaju sve uslove.</p>
                    </div>
                    <div>
                      <div className="n">3</div>
                      <strong>Putovanje</strong>
                      <p className="small muted">Sačuvajte let, smeštaj i plan obilaska i pošaljite ih sebi na mejl.</p>
                    </div>
                  </div>
                </div>
              ) : null}
            </section>
          </div>
        ) : null}

        {tab === "trips" ? <MyTrips trips={trips} openId={openTrip} onOpen={setOpenTrip} onReload={loadLibrary} /> : null}

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
        {tab === "integrations" ? <IntegrationsPanel mode={mode} onModeChange={setMode} /> : null}
      </main>

      <footer className="app-footer">
        TravelScout preporučuje i vodi vas na Wizz Air i Booking.com; ne rezerviše i ne naplaćuje. Nije povezan sa Wizz Air-om ni Booking.com-om.
      </footer>

      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}
      {itinerary ? <ItineraryView jobId={itinerary.jobId} pkg={itinerary.pkg} onClose={() => setItinerary(null)} /> : null}
      {comparing ? <CompareView packages={comparing} onClose={() => setComparing(null)} /> : null}
    </>
  );
}
