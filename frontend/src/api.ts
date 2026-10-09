import type {
  Integrations,
  InterpretedRequest,
  Itinerary,
  Job,
  ManualRecord,
  MailStatus,
  Meta,
  SavedSearch,
  SavedTrip,
  SearchConstraints,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return `${loc ? loc + ": " : ""}${d?.msg ?? "neispravna vrednost"}`;
      })
      .join("; ");
  }
  return "Nepoznata greška";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Server nije dostupan. Da li je backend pokrenut?", 0);
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = describe(body.detail);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const del = (path: string) => request<{ ok: boolean }>(path, { method: "DELETE" });

export const api = {
  meta: () => request<Meta>("/api/meta"),
  integrations: () => request<Integrations>("/api/integrations"),
  interpret: (constraints: SearchConstraints, text: string) =>
    post<InterpretedRequest>("/api/interpret", { constraints, text }),
  startSearch: (constraints: SearchConstraints, mode: "demo" | "live") =>
    post<{ job_id: string }>("/api/searches", { constraints, mode }),
  job: (id: string) => request<Job>(`/api/searches/${id}`),
  cancel: (id: string) => post<{ cancelled: boolean }>(`/api/searches/${id}/cancel`),
  selectFlights: (id: string, flightIds: string[]) => post<{ ok: boolean }>(`/api/searches/${id}/select`, { flight_ids: flightIds }),
  itinerary: (jobId: string, pkgId: string) =>
    post<Itinerary>(`/api/searches/${jobId}/packages/${encodeURIComponent(pkgId)}/itinerary`),
  exportUrl: (jobId: string, pkgId: string, format: "md" | "json") =>
    `/api/searches/${jobId}/packages/${encodeURIComponent(pkgId)}/export?format=${format}`,
  savedSearches: () => request<SavedSearch[]>("/api/saved-searches"),
  saveSearch: (name: string, constraints: SearchConstraints) =>
    post<{ id: string }>("/api/saved-searches", { name, constraints }),
  deleteSavedSearch: (id: string) => del(`/api/saved-searches/${id}`),
  trips: () => request<SavedTrip[]>("/api/trips"),
  saveTrip: (jobId: string, pkgId: string, note?: string) =>
    post<SavedTrip>("/api/trips", { job_id: jobId, package_id: pkgId, note: note || null, include_itinerary: true }),
  deleteTrip: (id: string) => del(`/api/trips/${id}`),
  tripExportUrl: (id: string, format: "md" | "json") => `/api/trips/${id}/export?format=${format}`,
  mailStatus: () => request<MailStatus>("/api/mail/status"),
  emailTrip: (id: string, to: string) => post<{ ok: boolean; to: string }>(`/api/trips/${id}/email`, { to: to || null }),
  manual: () => request<ManualRecord[]>("/api/manual"),
  addManual: (kind: "flight" | "accommodation", payload: unknown) => post<{ id: string }>(`/api/manual/${kind}`, payload),
  deleteManual: (id: string) => del(`/api/manual/${id}`),
  extractManual: (kind: "flight" | "accommodation", text: string) =>
    post<{ draft: Record<string, unknown>; note: string }>("/api/manual/extract", { kind, text }),
};
