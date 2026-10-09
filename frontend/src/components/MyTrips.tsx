import { useEffect, useState } from "react";
import { api } from "../api";
import type { MailStatus, SavedTrip } from "../types";
import { fmtMoney, localDate, money, timestamp } from "../format";
import { BathroomBadge, OriginBadge, Pass, Score } from "./Badges";
import { CostTable } from "./CostTable";
import { ItineraryBody } from "./ItineraryView";

function TripDetail({ trip, mail, onBack, onDeleted, onChanged }: {
  trip: SavedTrip;
  mail: MailStatus | null;
  onBack: () => void;
  onDeleted: () => void;
  onChanged: () => void;
}) {
  const pkg = trip.package;
  const f = pkg.flight;
  const o = pkg.accommodation.offer;
  const [to, setTo] = useState(mail?.default_to ?? "");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!to && mail?.default_to) setTo(mail.default_to);
  }, [mail, to]);

  const send = async () => {
    setSending(true);
    setResult(null);
    try {
      const r = await api.emailTrip(trip.id, to.trim());
      setResult({ ok: true, text: `Poslato na ${r.to}.` });
      onChanged();
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message });
    } finally {
      setSending(false);
    }
  };

  return (
    <article className="card panel trip-detail">
      <button className="btn ghost small" onClick={onBack} style={{ paddingLeft: 0 }}>
        ← Sva putovanja
      </button>
      <div className="row spread" style={{ alignItems: "flex-end", marginTop: 6 }}>
        <div>
          <div className="eyebrow">Sačuvano {timestamp(trip.created_at)}</div>
          <h2 style={{ fontSize: "2.2rem" }}>{pkg.destination_name}</h2>
          <div className="muted">
            {localDate(f.outbound.departure_local)} – {localDate(f.inbound.departure_local)} · {pkg.usable_time.nights} noći
          </div>
          <div className="row" style={{ gap: 14, marginTop: 6 }}>
            {pkg.data_origins.map((d) => (
              <OriginBadge key={d} origin={d} />
            ))}
          </div>
        </div>
        <div className="price-block">
          <div className="big tnum">{money(pkg.cost.known_total, pkg.cost.currency)}</div>
          <div className="small muted">{money(pkg.cost.per_person_known, pkg.cost.currency)} po osobi</div>
        </div>
      </div>
      {trip.note ? <p className="notice info small">{trip.note}</p> : null}

      <section className="detail-section">
        <div className="eyebrow">Pošalji na mejl</div>
        {mail?.configured ? (
          <div className="stack" style={{ gap: 8, marginTop: 8 }}>
            <div className="mail-box">
              <label className="field">
                Primalac
                <input type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="ime@gmail.com" />
              </label>
              <button className="btn dark" onClick={send} disabled={sending || !to.trim()}>
                {sending ? "Šaljem…" : "Pošalji putovanje"}
              </button>
            </div>
            <span className="tiny muted">
              Šalje se sa {mail.sender}: let, smeštaj, troškovi i plan obilaska, uz Markdown i JSON u prilogu.
              {trip.emailed_at ? ` Poslednje slanje: ${timestamp(trip.emailed_at)}.` : ""}
            </span>
            {result ? <div className={`notice ${result.ok ? "ok" : "bad"} small`}>{result.text}</div> : null}
          </div>
        ) : (
          <p className="small muted">
            Slanje nije podešeno. U <code>.env</code> dodajte <code>GMAIL_ADDRESS</code> i <code>GOOGLE_APP_PASSWORD</code>, pa restartujte server.
          </p>
        )}
      </section>

      <section className="detail-section split">
        <div>
          <div className="section-label">
            <span className="eyebrow">Let · {f.airline}</span>
            <span className="small tnum">{money(f.total_fare.amount, f.total_fare.currency)}</span>
          </div>
          <Pass leg={f.outbound} label="Tamo" />
          <Pass leg={f.inbound} label="Nazad" />
          {f.booking_url ? (
            <a className="btn small" href={f.booking_url} target="_blank" rel="noopener noreferrer" style={{ marginTop: 8 }}>
              Rezerviši na wizzair.com ↗
            </a>
          ) : null}
        </div>
        <div>
          <div className="section-label">
            <span className="eyebrow">Smeštaj · {o.property_type ?? "tip nepoznat"}</span>
            <span className="small tnum">{fmtMoney(o.total_price, 0)}</span>
          </div>
          <div className="stay-name">{o.property_name}</div>
          <div className="row" style={{ gap: 14, marginTop: 4 }}>
            <Score score={o.review_score} count={o.review_count} />
            <BathroomBadge status={o.bathroom} />
          </div>
          <dl className="kv">
            <dt>Soba</dt>
            <dd>{o.room_name ?? "—"}</dd>
            <dt>Noćenja</dt>
            <dd>
              {localDate(o.checkin)} → {localDate(o.checkout)}
            </dd>
            <dt>Otkazivanje</dt>
            <dd>{o.cancellation ?? "nepoznato"}</dd>
          </dl>
          {o.source_url ? (
            <a className="btn small" href={o.source_url} target="_blank" rel="noopener noreferrer" style={{ marginTop: 8 }}>
              Pogledaj na booking.com ↗
            </a>
          ) : null}
        </div>
      </section>

      <section className="detail-section">
        <details>
          <summary>Svi troškovi</summary>
          <CostTable cost={pkg.cost} />
        </details>
      </section>

      <section className="detail-section">
        <div className="eyebrow" style={{ marginBottom: 8 }}>
          Plan obilaska
        </div>
        {trip.itinerary ? <ItineraryBody data={trip.itinerary} /> : <p className="small muted">Plan nije sačuvan uz ovo putovanje.</p>}
      </section>

      <section className="detail-section row">
        <a className="btn small" href={api.tripExportUrl(trip.id, "md")} download>
          Preuzmi Markdown
        </a>
        <a className="btn small" href={api.tripExportUrl(trip.id, "json")} download>
          Preuzmi JSON
        </a>
        <button
          className="btn small danger"
          style={{ marginLeft: "auto" }}
          onClick={async () => {
            if (!window.confirm("Obrisati ovo putovanje?")) return;
            await api.deleteTrip(trip.id);
            onDeleted();
          }}
        >
          Obriši
        </button>
      </section>
    </article>
  );
}

export function MyTrips({ trips, openId, onOpen, onReload }: {
  trips: SavedTrip[];
  openId: string | null;
  onOpen: (id: string | null) => void;
  onReload: () => void;
}) {
  const [mail, setMail] = useState<MailStatus | null>(null);
  useEffect(() => {
    api.mailStatus().then(setMail).catch(() => setMail(null));
  }, []);

  const open = trips.find((t) => t.id === openId);
  if (open) {
    return (
      <TripDetail
        trip={open}
        mail={mail}
        onBack={() => onOpen(null)}
        onDeleted={() => {
          onOpen(null);
          onReload();
        }}
        onChanged={onReload}
      />
    );
  }

  if (trips.length === 0) {
    return (
      <div className="card empty">
        <div className="eyebrow">Moja putovanja</div>
        <h2>Još niste sačuvali nijedno putovanje</h2>
        <p className="muted">
          Kada pronađete kombinaciju leta i smeštaja koja vam odgovara, kliknite „Sačuvaj putovanje“. Čuvamo let, smeštaj, troškove i plan
          obilaska, a odavde ga možete poslati sebi na mejl.
        </p>
      </div>
    );
  }

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div>
        <div className="eyebrow">Moja putovanja</div>
        <h2 style={{ fontSize: "1.8rem" }}>{trips.length === 1 ? "1 sačuvano putovanje" : `${trips.length} sačuvana putovanja`}</h2>
        <p className="small muted" style={{ margin: "4px 0 0" }}>
          Snimci u trenutku čuvanja – cene i dostupnost proverite ponovo pre rezervacije.
        </p>
      </div>
      {trips.map((t) => {
        const p = t.package;
        return (
          <button key={t.id} className="card ticket" onClick={() => onOpen(t.id)}>
            <div>
              <h3>{p.destination_name}</h3>
              <div className="small muted">
                {localDate(p.flight.outbound.departure_local)} – {localDate(p.flight.inbound.departure_local)} · {p.usable_time.nights} noći ·{" "}
                {p.accommodation.offer.property_name}
              </div>
              <div className="row" style={{ gap: 14, marginTop: 6 }}>
                {p.data_origins.map((d) => (
                  <OriginBadge key={d} origin={d} />
                ))}
                {t.itinerary ? <span className="badge neutral">Plan obilaska</span> : null}
                {t.emailed_at ? <span className="badge ok">Poslato mejlom</span> : null}
              </div>
            </div>
            <div className="price-block">
              <div className="big tnum" style={{ fontSize: "1.6rem" }}>
                {money(p.cost.known_total, p.cost.currency)}
              </div>
              <div className="tiny muted">otvori →</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
