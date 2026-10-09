// Mirrors backend/app/domain/models.py (money values arrive as decimal strings).

export type DataOrigin = "live" | "cached" | "manual" | "demo" | "unverified";
export type Money = { amount: string; currency: string };

export type Evidence = {
  origin: DataOrigin;
  provider: string;
  source_url: string | null;
  retrieved_at: string | null;
  notes: string[];
};

export type Party = { adults: number; children: number; child_ages: number[]; infants: number };
export type Baggage = { cabin_trolley_10kg: number; checked_bags_20kg: number; checked_bags_32kg: number };
export type RankingPreferences = { quality_weight: number; location_weight: number; time_weight: number };

export type SearchConstraints = {
  origins: string[];
  date_mode: "exact" | "flexible";
  departure_date: string | null;
  return_date: string | null;
  window_start: string | null;
  window_end: string | null;
  min_nights: number;
  max_nights: number;
  destinations: string[];
  anywhere: boolean;
  party: Party;
  budget_amount: string | null;
  budget_scope: "per_person" | "group";
  budget_currency: string;
  baggage: Baggage;
  direct_only: boolean;
  interests: string[];
  pace: "relaxed" | "moderate" | "intense";
  min_review_score: string;
  max_distance_km: string | null;
  rooms: number;
  display_currency: "EUR" | "RSD";
  ranking: RankingPreferences;
};

export type InterpretationNote = { field: string; message: string; severity: "info" | "warning" };
export type InterpretedRequest = { constraints: SearchConstraints; notes: InterpretationNote[]; used_llm: boolean };

export type FlightLeg = {
  origin: string;
  destination: string;
  departure_local: string;
  departure_tz: string | null;
  arrival_local: string | null;
  arrival_tz: string | null;
  arrival_is_estimate: boolean;
  flight_number: string | null;
  fare_per_seat: Money;
  direct: boolean;
};

export type PassengerPrice = {
  passenger_type: "adult" | "child" | "infant";
  count: number;
  fare_per_person: Money | null;
  note: string | null;
};

export type BaggageQuote = { item: string; quantity: number; unit_price: Money | null; note: string | null };

export type FlightOption = {
  id: string;
  airline: string;
  outbound: FlightLeg;
  inbound: FlightLeg;
  passengers: PassengerPrice[];
  total_fare: Money;
  total_fare_by_leg: Money[];
  included_baggage: string;
  baggage_quotes: BaggageQuote[];
  fare_type: "regular" | "membership";
  fare_is_confirmed_quote: boolean;
  evidence: Evidence;
  booking_url: string | null;
};

export type BathroomStatus = "ensuite" | "private" | "private_external" | "entire_unit" | "shared" | "unknown";

export type Fee = { name: string; amount: Money | null; included_in_total: boolean; mandatory: boolean };

export type AccommodationOffer = {
  id: string;
  destination: string;
  property_name: string;
  property_type: string | null;
  location: string | null;
  latitude: number | null;
  longitude: number | null;
  source_url: string | null;
  review_score: string | null;
  review_count: number | null;
  room_name: string | null;
  room_privacy: "private_room" | "entire_unit" | "shared_space" | "unknown";
  bathroom: BathroomStatus;
  bathroom_evidence: string | null;
  max_occupancy: number | null;
  rooms: number;
  checkin: string;
  checkout: string;
  available_for_dates: boolean | null;
  base_price: Money | null;
  fees: Fee[];
  total_price: Money | null;
  cancellation: string | null;
  prepayment: string | null;
  distance_km: string | null;
  distance_reference: string | null;
  evidence: Evidence;
};

export type EligibilityResult = {
  status: "eligible" | "needs_verification" | "ineligible";
  failed: string[];
  unverified: string[];
  messages: string[];
};

export type AssessedOffer = { offer: AccommodationOffer; eligibility: EligibilityResult };

export type FxRate = { base: string; quote: string; rate: string; source: string; as_of: string; is_fallback: boolean };

export type CostLine = {
  label: string;
  amount: Money | null;
  category: "mandatory" | "extra" | "estimate" | "unknown";
  note: string | null;
};

export type CostBreakdown = {
  currency: string;
  lines: CostLine[];
  known_mandatory: string;
  known_extras: string;
  estimated: string;
  unknown_items: string[];
  known_total: string;
  comparable_total: string;
  per_person_known: string;
  is_complete: boolean;
  fx_rates: FxRate[];
};

export type UsableTime = {
  nights: number;
  full_days: number;
  usable_hours: number;
  arrival_note: string | null;
  departure_note: string | null;
  overnight_arrival: boolean;
};

export type TripPackage = {
  id: string;
  destination: string;
  destination_name: string;
  country: string | null;
  flight: FlightOption;
  accommodation: AssessedOffer;
  cost: CostBreakdown;
  usable_time: UsableTime;
  rank: number;
  rank_score: string;
  highlights: string[];
  caveats: string[];
  data_origins: DataOrigin[];
};

export type Diagnostic = { stage: string; code: string; message: string; count: number | null };

export type ProviderRunStatus = {
  provider: string;
  status: "ok" | "partial" | "failed" | "skipped" | "disabled";
  message: string | null;
  origin: DataOrigin | null;
  checked_at: string | null;
};

export type SearchResult = {
  constraints: SearchConstraints;
  mode: "demo" | "live";
  packages: TripPackage[];
  flight_shortlist: FlightOption[];
  destination_cities: Record<string, string>;
  needs_verification: AssessedOffer[];
  rejected_counts: Record<string, number>;
  diagnostics: Diagnostic[];
  suggestions: string[];
  provider_status: ProviderRunStatus[];
  agent_notes: string[];
  used_llm: boolean;
  fx_status: string;
  partial: boolean;
  generated_at: string;
};

export type ProgressEvent = {
  stage: string;
  message: string;
  status: "running" | "done" | "warning" | "error" | "skipped";
  agent: string | null;
  tool: string | null;
  at: string;
};

export type JobStatus = "queued" | "running" | "done" | "partial" | "failed" | "cancelled";

export type Job = {
  id: string;
  created_at: string;
  updated_at: string;
  status: JobStatus;
  mode: "demo" | "live";
  request: SearchConstraints;
  progress: ProgressEvent[];
  result: SearchResult | null;
  error: string | null;
};

export type PointOfInterest = {
  id: string;
  name: string;
  category: string;
  latitude: number;
  longitude: number;
  opening_hours: string | null;
  fee: "yes" | "no" | null;
  website: string | null;
  wikipedia: string | null;
  map_url: string;
  source: string;
  origin: DataOrigin;
};

export type ItineraryItem = {
  start: string;
  end: string;
  title: string;
  kind: "sight" | "meal" | "transfer" | "break" | "free_time" | "checkin" | "checkout";
  poi_id: string | null;
  area: string | null;
  description: string | null;
  paid: boolean | null;
  map_url: string | null;
  source_url: string | null;
  opening_hours: string | null;
  verified: boolean;
};

export type ItineraryDay = {
  date: string;
  title: string;
  area: string | null;
  items: ItineraryItem[];
  est_sightseeing_eur: string;
  est_transport_eur: string;
};

export type TransferOption = {
  mode: string;
  description: string;
  est_duration_min: number | null;
  est_cost_eur: string | null;
  verified: boolean;
  source_url: string | null;
};

export type Itinerary = {
  package_id: string;
  destination_name: string;
  days: ItineraryDay[];
  transfers: TransferOption[];
  pois: PointOfInterest[];
  sources: string[];
  disclaimers: string[];
  used_llm: boolean;
  generated_at: string;
};

export type Meta = {
  origins: { iata: string; city: string; country: string }[];
  demo_destinations: string[];
  llm_available: boolean;
  model: string;
  live_flights: boolean;
  live_accommodation: boolean;
  max_shortlist: number;
  defaults: SearchConstraints;
};

export type IntegrationItem = {
  id: string;
  name: string;
  status: "active" | "disabled" | "missing" | "unavailable" | "error";
  detail: string;
  data: string;
};

export type Integrations = {
  items: IntegrationItem[];
  last_run: ProviderRunStatus[];
  env_file_found: boolean;
  checked_at: string;
};

export type SavedSearch = { id: string; created_at: string; name: string; constraints: SearchConstraints };
export type Favorite = { id: string; created_at: string; job_id: string | null; package: TripPackage };
export type ManualRecord = { id: string; kind: "flight" | "accommodation"; created_at: string; payload: Record<string, unknown> };
