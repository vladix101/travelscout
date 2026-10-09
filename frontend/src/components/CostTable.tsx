import type { CostBreakdown } from "../types";
import { money } from "../format";

const CATEGORY: Record<string, string> = {
  mandatory: "obavezno",
  extra: "dodatak",
  estimate: "procena",
  unknown: "nepoznato",
};

export function CostTable({ cost }: { cost: CostBreakdown }) {
  const cur = cost.currency;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <table className="cost">
        <thead>
          <tr>
            <th>Stavka</th>
            <th>Vrsta</th>
            <th className="num">Iznos</th>
          </tr>
        </thead>
        <tbody>
          {cost.lines.map((ln, i) => (
            <tr key={i} className={`cat-${ln.category}`}>
              <td>
                {ln.label}
                {ln.note ? <div className="tiny muted">{ln.note}</div> : null}
              </td>
              <td>{CATEGORY[ln.category]}</td>
              <td className="num">{ln.amount ? money(ln.amount.amount, ln.amount.currency, 2) : "?"}</td>
            </tr>
          ))}
          <tr className="sum">
            <td colSpan={2}>Poznati troškovi (obavezno + poznati dodaci)</td>
            <td className="num">{money(cost.known_total, cur, 2)}</td>
          </tr>
          <tr>
            <td colSpan={2} className="muted">+ procene (transfer, hrana, prevoz, aktivnosti)</td>
            <td className="num muted">{money(cost.estimated, cur, 2)}</td>
          </tr>
          <tr className="sum">
            <td colSpan={2}>Uporedivi ukupni iznos</td>
            <td className="num">{money(cost.comparable_total, cur, 2)}</td>
          </tr>
        </tbody>
      </table>
      {cost.unknown_items.length > 0 ? (
        <div className="notice bad small">
          <strong>Cena nije kompletna.</strong> Nepoznati troškovi nisu uračunati kao nula:
          <ul>
            {cost.unknown_items.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {cost.fx_rates.length > 0 ? (
        <div className="tiny muted">
          Kurs:{" "}
          {cost.fx_rates
            .map((r) => `1 EUR = ${Number(r.rate).toLocaleString("sr-Latn-RS", { maximumFractionDigits: 2 })} ${r.quote} (${r.as_of})`)
            .join(" · ")}
        </div>
      ) : null}
    </div>
  );
}
