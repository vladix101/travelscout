import type { BathroomStatus, DataOrigin, Money } from "./types";

const LOCALE = "sr-Latn-RS";

export function money(amount: string | number | null | undefined, currency: string, digits = 0): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const n = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(n)) return "—";
  return `${n.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency}`;
}

export const fmtMoney = (m: Money | null | undefined, digits = 2) => (m ? money(m.amount, m.currency, digits) : "nepoznato");

const dateFmt = new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "2-digit", year: "numeric" });
const dayFmt = new Intl.DateTimeFormat(LOCALE, { weekday: "short", day: "numeric", month: "short" });

/** Backend sends naive local wall-clock times; parse them without applying the browser's zone. */
function parts(iso: string): { y: number; m: number; d: number; hh: number; mm: number } {
  const [datePart = "", timePart = "00:00"] = iso.split("T");
  const [y = 0, m = 1, d = 1] = datePart.split("-").map(Number);
  const [hh = 0, mm = 0] = timePart.split(":").map(Number);
  return { y, m, d, hh, mm };
}

export function localDate(iso: string): string {
  const p = parts(iso);
  return dateFmt.format(new Date(p.y, p.m - 1, p.d));
}

export function localDay(iso: string): string {
  const p = parts(iso);
  return dayFmt.format(new Date(p.y, p.m - 1, p.d));
}

export function localTime(iso: string | null): string {
  if (!iso) return "—";
  const p = parts(iso);
  return `${String(p.hh).padStart(2, "0")}:${String(p.mm).padStart(2, "0")}`;
}

export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(LOCALE, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function tzShort(tz: string | null): string {
  if (!tz) return "zona nepoznata";
  return tz.split("/").pop()?.replace("_", " ") ?? tz;
}

export const ORIGIN_LABEL: Record<DataOrigin, string> = {
  live: "Uživo",
  cached: "Keš",
  manual: "Ručni unos",
  demo: "DEMO",
  unverified: "Neprovereno",
};

export const BATHROOM_LABEL: Record<BathroomStatus, string> = {
  ensuite: "Privatno kupatilo u sobi",
  private: "Privatno kupatilo",
  private_external: "Privatno kupatilo VAN sobe",
  entire_unit: "Ceo smeštaj, sopstveno kupatilo",
  shared: "Zajedničko kupatilo",
  unknown: "Kupatilo nepotvrđeno",
};

export const RULE_LABEL: Record<string, string> = {
  review_score_missing: "ocena nepoznata",
  review_score_below_min: "ocena ispod 7.5",
  hostel: "hostel",
  shared_sleeping_space: "deljena spavaonica",
  room_privacy_unknown: "privatnost sobe nepotvrđena",
  shared_bathroom: "zajedničko kupatilo",
  bathroom_unconfirmed: "kupatilo nepotvrđeno",
  occupancy_unknown: "kapacitet nepoznat",
  occupancy_insufficient: "premali kapacitet",
  dates_mismatch: "pogrešni datumi",
  availability_unconfirmed: "dostupnost nepotvrđena",
  not_available: "nije dostupno",
  price_unknown: "cena nepoznata",
  too_far: "predaleko",
  distance_unknown: "udaljenost nepoznata",
  source_unverified: "neprovereni izvor",
  rooms_mismatch: "broj soba",
};

export function nightsBetween(a: string, b: string): number {
  const pa = parts(a);
  const pb = parts(b);
  return Math.round((Date.UTC(pb.y, pb.m - 1, pb.d) - Date.UTC(pa.y, pa.m - 1, pa.d)) / 86_400_000);
}
