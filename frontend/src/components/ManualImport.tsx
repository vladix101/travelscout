import { useEffect, useState } from "react";
import { api } from "../api";
import type { ManualRecord, Meta } from "../types";
import { timestamp } from "../format";

type FlightForm = {
  origin: string;
  destination: string;
  out_dep: string;
  out_arr: string;
  out_no: string;
  in_dep: string;
  in_arr: string;
  in_no: string;
  price_out: string;
  price_in: string;
  currency: string;
  bag20: string;
  source_url: string;
};

type StayForm = {
  destination: string;
  property_name: string;
  property_type: string;
  location: string;
  source_url: string;
  review_score: string;
  review_count: string;
  room_name: string;
  room_privacy: string;
  bathroom: string;
  bathroom_evidence: string;
  max_occupancy: string;
  rooms: string;
  checkin: string;
  checkout: string;
  availability_confirmed: boolean;
  total_price: string;
  currency: string;
  city_tax: string;
  cancellation: string;
  distance_km: string;
};

const EMPTY_FLIGHT: FlightForm = {
  origin: "BEG", destination: "", out_dep: "", out_arr: "", out_no: "", in_dep: "", in_arr: "", in_no: "",
  price_out: "", price_in: "", currency: "EUR", bag20: "", source_url: "",
};
const EMPTY_STAY: StayForm = {
  destination: "", property_name: "", property_type: "Hotel", location: "", source_url: "", review_score: "", review_count: "",
  room_name: "", room_privacy: "private_room", bathroom: "unknown", bathroom_evidence: "", max_occupancy: "2", rooms: "1",
  checkin: "", checkout: "", availability_confirmed: false, total_price: "", currency: "EUR", city_tax: "", cancellation: "", distance_km: "",
};

const opt = (s: string) => (s.trim() === "" ? null : s.trim());

function toFlightPayload(f: FlightForm) {
  return {
    origin: f.origin.toUpperCase(),
    destination: f.destination.toUpperCase(),
    outbound: { departure_local: f.out_dep, arrival_local: opt(f.out_arr), flight_number: opt(f.out_no) },
    inbound: { departure_local: f.in_dep, arrival_local: opt(f.in_arr), flight_number: opt(f.in_no) },
    price_per_seat_outbound: f.price_out,
    price_per_seat_inbound: f.price_in,
    currency: f.currency,
    baggage_prices: f.bag20 ? { checked20: f.bag20 } : {},
    source_url: opt(f.source_url),
  };
}

function toStayPayload(s: StayForm) {
  return {
    destination: s.destination.toUpperCase(),
    property_name: s.property_name,
    property_type: opt(s.property_type),
    location: opt(s.location),
    source_url: opt(s.source_url),
    review_score: opt(s.review_score),
    review_count: s.review_count ? Number(s.review_count) : null,
    room_name: opt(s.room_name),
    room_privacy: s.room_privacy,
    bathroom: s.bathroom,
    bathroom_evidence: opt(s.bathroom_evidence),
    max_occupancy: s.max_occupancy ? Number(s.max_occupancy) : null,
    rooms: Number(s.rooms || 1),
    checkin: s.checkin,
    checkout: s.checkout,
    availability_confirmed: s.availability_confirmed,
    total_price: opt(s.total_price),
    currency: s.currency,
    fees: s.city_tax ? [{ name: "Boravišna taksa", amount: s.city_tax, included_in_total: false }] : [],
    cancellation: opt(s.cancellation),
    distance_km: opt(s.distance_km),
  };
}

function Text<T extends Record<string, unknown>>({ form, k, label, set, type = "text", hint }: {
  form: T; k: keyof T & string; label: string; set: (k: keyof T, v: string) => void; type?: string; hint?: string;
}) {
  return (
    <label className="field">
      {label}
      <input type={type} value={String(form[k] ?? "")} onChange={(e) => set(k, e.target.value)} step={type === "number" ? "any" : undefined} />
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

export function ManualImport({ meta, onChanged }: { meta: Meta; onChanged: () => void }) {
  const [records, setRecords] = useState<ManualRecord[]>([]);
  const [flight, setFlight] = useState<FlightForm>(EMPTY_FLIGHT);
  const [stay, setStay] = useState<StayForm>(EMPTY_STAY);
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [paste, setPaste] = useState("");
  const [pasteKind, setPasteKind] = useState<"flight" | "accommodation">("accommodation");
  const [extracting, setExtracting] = useState(false);

  const load = () => api.manual().then(setRecords).catch(() => setRecords([]));
  useEffect(() => {
    load();
  }, []);

  const setF = (k: keyof FlightForm, v: string) => setFlight((f) => ({ ...f, [k]: v }));
  const setS = (k: keyof StayForm, v: string) => setStay((s) => ({ ...s, [k]: v }));

  const save = async (kind: "flight" | "accommodation") => {
    setMsg(null);
    try {
      await api.addManual(kind, kind === "flight" ? toFlightPayload(flight) : toStayPayload(stay));
      setMsg({ tone: "info", text: "Sačuvano. Uvezeni podaci se koriste u pretragama sa stvarnim podacima i označeni su kao „Ručni unos“." });
      if (kind === "flight") setFlight(EMPTY_FLIGHT);
      else setStay(EMPTY_STAY);
      load();
      onChanged();
    } catch (e) {
      setMsg({ tone: "bad", text: `Podaci nisu validni: ${(e as Error).message}` });
    }
  };

  const extract = async () => {
    setExtracting(true);
    setMsg(null);
    try {
      const { draft } = await api.extractManual(pasteKind, paste);
      if (pasteKind === "accommodation") {
        const d = draft as Record<string, unknown>;
        const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
        setStay({
          ...EMPTY_STAY,
          destination: str(d.destination), property_name: str(d.property_name), property_type: str(d.property_type),
          location: str(d.location), source_url: str(d.source_url), review_score: str(d.review_score), review_count: str(d.review_count),
          room_name: str(d.room_name), room_privacy: str(d.room_privacy) || "unknown", bathroom: str(d.bathroom) || "unknown",
          bathroom_evidence: str(d.bathroom_evidence), max_occupancy: str(d.max_occupancy), rooms: str(d.rooms) || "1",
          checkin: str(d.checkin), checkout: str(d.checkout), total_price: str(d.total_price), currency: str(d.currency) || "EUR",
          cancellation: str(d.cancellation), distance_km: str(d.distance_km),
        });
      } else {
        const d = draft as Record<string, any>;
        const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
        setFlight({
          ...EMPTY_FLIGHT,
          origin: str(d.origin), destination: str(d.destination),
          out_dep: str(d.outbound?.departure_local).slice(0, 16), out_arr: str(d.outbound?.arrival_local).slice(0, 16), out_no: str(d.outbound?.flight_number),
          in_dep: str(d.inbound?.departure_local).slice(0, 16), in_arr: str(d.inbound?.arrival_local).slice(0, 16), in_no: str(d.inbound?.flight_number),
          price_out: str(d.price_per_seat_outbound), price_in: str(d.price_per_seat_inbound), currency: str(d.currency) || "EUR",
          source_url: str(d.source_url),
        });
      }
      setMsg({ tone: "warn", text: "Nacrt je popunjen iz teksta. Proverite svako polje pre čuvanja – posebno kupatilo i dostupnost." });
    } catch (e) {
      setMsg({ tone: "bad", text: (e as Error).message });
    } finally {
      setExtracting(false);
    }
  };

  return (
    <div className="stack">
      <section className="card panel stack">
        <h2>Ručni uvoz ponuda</h2>
        <p className="small muted" style={{ margin: 0 }}>
          Unesite let ili smeštaj koji ste sami pronašli (npr. na wizzair.com ili booking.com). Isti strogi uslovi važe i za uvezeni smeštaj – ako
          kupatilo, ocena ili dostupnost nisu potvrđeni, ponuda ide u „Potrebna provera“.
        </p>
        {msg ? <div className={`notice ${msg.tone} small`}>{msg.text}</div> : null}
        <details>
          <summary>Nalepi tekst ponude i izdvoji podatke (AI)</summary>
          <div className="stack">
            <div className="segmented">
              <button type="button" aria-pressed={pasteKind === "accommodation"} onClick={() => setPasteKind("accommodation")}>
                Smeštaj
              </button>
              <button type="button" aria-pressed={pasteKind === "flight"} onClick={() => setPasteKind("flight")}>
                Let
              </button>
            </div>
            <textarea value={paste} maxLength={6000} onChange={(e) => setPaste(e.target.value)} placeholder="Nalepite tekst sa stranice ponude…" />
            <span className="tiny muted">Tekst se tretira kao nepouzdani podatak, nikada kao instrukcija agentu.</span>
            <button className="btn small" disabled={!meta.llm_available || paste.trim().length < 10 || extracting} onClick={extract}>
              {extracting ? "Izdvajam…" : meta.llm_available ? "Izdvoji u formu" : "Zahteva OPENAI_API_KEY"}
            </button>
          </div>
        </details>
      </section>

      <div className="split">
        <section className="card panel stack">
          <h3>Let (povratni)</h3>
          <div className="grid-2">
            <Text form={flight} k="origin" label="Polazak (IATA)" set={setF} />
            <Text form={flight} k="destination" label="Destinacija (IATA)" set={setF} />
            <Text form={flight} k="out_dep" label="Polazak tamo (lokalno)" type="datetime-local" set={setF} />
            <Text form={flight} k="out_arr" label="Dolazak tamo (lokalno)" type="datetime-local" set={setF} />
            <Text form={flight} k="out_no" label="Broj leta tamo" set={setF} />
            <Text form={flight} k="price_out" label="Cena po sedištu tamo" type="number" set={setF} />
            <Text form={flight} k="in_dep" label="Polazak nazad (lokalno)" type="datetime-local" set={setF} />
            <Text form={flight} k="in_arr" label="Dolazak nazad (lokalno)" type="datetime-local" set={setF} />
            <Text form={flight} k="in_no" label="Broj leta nazad" set={setF} />
            <Text form={flight} k="price_in" label="Cena po sedištu nazad" type="number" set={setF} />
            <label className="field">
              Valuta
              <select value={flight.currency} onChange={(e) => setF("currency", e.target.value)}>
                {["EUR", "RSD", "GBP", "HUF", "PLN", "CHF"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <Text form={flight} k="bag20" label="Cena prtljaga 20 kg (po pravcu)" type="number" set={setF} hint="prazno = nepoznato" />
          </div>
          <Text form={flight} k="source_url" label="Link (https)" type="url" set={setF} />
          <button className="btn primary" onClick={() => save("flight")}>
            Sačuvaj let
          </button>
        </section>

        <section className="card panel stack">
          <h3>Smeštaj</h3>
          <div className="grid-2">
            <Text form={stay} k="destination" label="Destinacija (IATA)" set={setS} hint="aerodrom uz koji ide smeštaj" />
            <Text form={stay} k="property_name" label="Naziv objekta" set={setS} />
            <label className="field">
              Tip
              <select value={stay.property_type} onChange={(e) => setS("property_type", e.target.value)}>
                {["Hotel", "Apartment", "Guest house", "Bed and breakfast", "Holiday home", "Hostel"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
            <Text form={stay} k="location" label="Lokacija" set={setS} />
            <Text form={stay} k="review_score" label="Ocena (0–10)" type="number" set={setS} hint="prazno = nepoznato" />
            <Text form={stay} k="review_count" label="Broj recenzija" type="number" set={setS} />
            <Text form={stay} k="room_name" label="Soba / jedinica" set={setS} />
            <label className="field">
              Privatnost
              <select value={stay.room_privacy} onChange={(e) => setS("room_privacy", e.target.value)}>
                <option value="private_room">Privatna soba</option>
                <option value="entire_unit">Ceo smeštaj</option>
                <option value="shared_space">Deljena soba / spavaonica</option>
                <option value="unknown">Nepoznato</option>
              </select>
            </label>
            <label className="field">
              Kupatilo (za ovu sobu)
              <select value={stay.bathroom} onChange={(e) => setS("bathroom", e.target.value)}>
                <option value="ensuite">Privatno, u sobi</option>
                <option value="private">Privatno (nije navedeno gde)</option>
                <option value="private_external">Privatno, van sobe</option>
                <option value="entire_unit">Ceo smeštaj, sopstveno</option>
                <option value="shared">Zajedničko</option>
                <option value="unknown">Nepoznato</option>
              </select>
            </label>
            <Text form={stay} k="max_occupancy" label="Maks. osoba po sobi" type="number" set={setS} />
            <Text form={stay} k="checkin" label="Prijava" type="date" set={setS} />
            <Text form={stay} k="checkout" label="Odjava" type="date" set={setS} />
            <Text form={stay} k="total_price" label="Ukupna cena boravka" type="number" set={setS} />
            <label className="field">
              Valuta
              <select value={stay.currency} onChange={(e) => setS("currency", e.target.value)}>
                {["EUR", "RSD", "GBP", "HUF", "PLN", "CHF"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <Text form={stay} k="city_tax" label="Boravišna taksa (ako nije u ceni)" type="number" set={setS} />
            <Text form={stay} k="distance_km" label="Km od centra" type="number" set={setS} />
          </div>
          <Text form={stay} k="bathroom_evidence" label="Dokaz za kupatilo (citat sa stranice)" set={setS} />
          <Text form={stay} k="cancellation" label="Otkazivanje" set={setS} />
          <Text form={stay} k="source_url" label="Link (https)" type="url" set={setS} />
          <label className="check">
            <input type="checkbox" checked={stay.availability_confirmed} onChange={(e) => setStay((s) => ({ ...s, availability_confirmed: e.target.checked }))} />
            Proverio/la sam dostupnost za tačne datume i cenu
          </label>
          <button className="btn primary" onClick={() => save("accommodation")}>
            Sačuvaj smeštaj
          </button>
        </section>
      </div>

      <section className="card panel stack">
        <h3>Uvezeno ({records.length})</h3>
        {records.length === 0 ? <p className="small muted">Još nema ručno unetih ponuda.</p> : null}
        {records.map((r) => (
          <div key={r.id} className="row spread small" style={{ borderTop: "1px solid var(--border)", paddingTop: 6 }}>
            <span>
              <span className="badge manual">{r.kind === "flight" ? "Let" : "Smeštaj"}</span>{" "}
              {r.kind === "flight"
                ? `${String(r.payload.origin)} → ${String(r.payload.destination)}`
                : `${String(r.payload.property_name)} (${String(r.payload.destination)}) ${String(r.payload.checkin)} → ${String(r.payload.checkout)}`}
              <span className="tiny muted"> · {timestamp(r.created_at)}</span>
            </span>
            <button
              className="btn small danger"
              onClick={async () => {
                await api.deleteManual(r.id);
                load();
                onChanged();
              }}
            >
              Obriši
            </button>
          </div>
        ))}
      </section>
    </div>
  );
}
