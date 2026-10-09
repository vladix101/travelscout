import type { ReactNode } from "react";
import type { TripPackage } from "../types";
import { BATHROOM_LABEL, ORIGIN_LABEL, fmtMoney, localDate, localTime, money } from "../format";
import { Modal } from "./Modal";

export function CompareView({ packages, onClose }: { packages: TripPackage[]; onClose: () => void }) {
  const rows: [string, (p: TripPackage) => ReactNode][] = [
    ["Rang", (p) => `#${p.rank}`],
    ["Datumi", (p) => `${localDate(p.flight.outbound.departure_local)} – ${localDate(p.flight.inbound.departure_local)}`],
    ["Noći / sati obilaska", (p) => `${p.usable_time.nights} / ${p.usable_time.usable_hours.toFixed(0)} h`],
    ["Let tamo", (p) => `${localTime(p.flight.outbound.departure_local)} → ${localTime(p.flight.outbound.arrival_local)}${p.flight.outbound.arrival_is_estimate ? " (procena)" : ""}`],
    ["Let nazad", (p) => `${localTime(p.flight.inbound.departure_local)} → ${localTime(p.flight.inbound.arrival_local)}`],
    ["Cena letova", (p) => money(p.flight.total_fare.amount, p.flight.total_fare.currency)],
    ["Smeštaj", (p) => `${p.accommodation.offer.property_name} (${p.accommodation.offer.property_type ?? "?"})`],
    ["Ocena", (p) => p.accommodation.offer.review_score ?? "nepoznata"],
    ["Kupatilo", (p) => BATHROOM_LABEL[p.accommodation.offer.bathroom]],
    ["Udaljenost", (p) => (p.accommodation.offer.distance_km ? `${p.accommodation.offer.distance_km} km` : "—")],
    ["Cena smeštaja", (p) => fmtMoney(p.accommodation.offer.total_price)],
    ["Otkazivanje", (p) => p.accommodation.offer.cancellation ?? "—"],
    ["Poznati troškovi", (p) => <strong>{money(p.cost.known_total, p.cost.currency)}</strong>],
    ["Po osobi", (p) => money(p.cost.per_person_known, p.cost.currency)],
    ["Procene", (p) => money(p.cost.estimated, p.cost.currency)],
    ["Uporedivo ukupno", (p) => money(p.cost.comparable_total, p.cost.currency)],
    ["Nepoznato", (p) => (p.cost.unknown_items.length ? p.cost.unknown_items.join("; ") : "—")],
    ["Izvori", (p) => p.data_origins.map((o) => ORIGIN_LABEL[o]).join(", ")],
  ];
  return (
    <Modal title="Poređenje putovanja" onClose={onClose}>
      <div className="compare">
        <table>
          <thead>
            <tr>
              <th />
              {packages.map((p) => (
                <th key={p.id}>
                  {p.destination_name}
                  <div className="tiny muted">{p.destination}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, fn]) => (
              <tr key={label}>
                <th scope="row" className="muted small">
                  {label}
                </th>
                {packages.map((p) => (
                  <td key={p.id}>{fn(p)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
