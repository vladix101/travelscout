# External data access: what exists, what TravelScout uses

Investigated October 2026. Provider terms and endpoints change; re-check before relying on any of this.

## Summary

| Need | Official / authorized option | Usable by an individual? | What TravelScout does |
|---|---|---|---|
| Wizz Air fares | None. Wizz Air publishes no public or partner fare API. | — | Opt-in reader for the two public, unauthenticated endpoints the wizzair.com site itself calls (route map, fare calendar). |
| Wizz Air fares via GDS/aggregators | Amadeus Self-Service (closed: new sign-ups paused Feb 2026, keys disabled 17 Jul 2026). Kiwi Tequila (closed to new partners). Wizz Air is a low-cost carrier largely absent from GDS content anyway. | No | Not used. No other airline/aggregator is ever relabelled as "Wizz Air". |
| Booking.com accommodation | Booking.com **Demand API** (developers.booking.com). Requires registration as a *Managed Affiliate Partner*, a signed contract and Partner Centre credentials. Booking.com partner terms also restrict AI-based systems without written approval. | No (contract required) | Opt-in, visible Playwright browser reading booking.com pages for the exact dates, low volume, cached. |
| Attractions / map data | OpenStreetMap: Nominatim (geocoding) and Overpass API. Free, documented usage policies (≤1 req/s for Nominatim, identifying User-Agent). | Yes | Used for itinerary facts (names, coordinates, `opening_hours`/`fee` tags, websites). |
| Exchange rates | ECB reference rates via Frankfurter (free, documented). RSD is not an ECB currency: National Bank of Serbia official middle rate via kurs.resenje.org (documented public API). | Yes | Used, cached 6 h, every converted total lists the rates, source and date. Static fallback rates are flagged. |

## Wizz Air details

Endpoints (version string scraped from the public homepage, e.g. `be.wizzair.com/29.20.0/Api`):

* `GET /asset/map?languageCode=en-gb`: airports, coordinates, countries, connections (`isDirectFlight`). Works without login or challenge.
* `POST /search/timetable`: lowest **regular** fare per day per direction, departure times, fare currency = departure airport currency. Works without login or challenge. TravelScout always sends `priceType: "regular"` and never assumes Wizz Discount Club membership.
* `POST /search/search`, `/booking/ancillaries`, etc.: flight numbers, arrival times, fare bundles, bag prices. **Protected by Kasada bot detection** (HTTP 429 + JavaScript challenge). TravelScout does **not** attempt to bypass this.

Consequences, shown in the UI:
* Fares are "lowest published fare per seat for that day", not a confirmed quote for the whole party (low fare buckets can sell out for larger groups).
* Arrival times are **estimates** (great-circle distance + time zones) and flagged as such; flight numbers are unknown.
* Bag prices are **unknown** and listed as unknown costs (never zero). Users can import exact prices manually.
* Wizz Air charges children the adult fare; infant fees are unknown in live mode.

These are undocumented endpoints. They may change or stop working at any time, and using them may conflict with Wizz Air's terms of use. Keep usage personal and low-volume.

## Booking.com details

Plain HTTP requests receive an AWS WAF JavaScript challenge, and headless Chromium is challenged on every request. A normal (non-headless) Chromium (Playwright, no stealth plugins, no fingerprint spoofing, no proxies) loads results normally. By default its window is placed off-screen (`BOOKING_WINDOW=offscreen`), so nothing pops up; `visible` shows it. Flow per selected trip:

1. Open booking.com once (the JS challenge sets a session cookie).
2. Search results for the city and exact dates, sorted by price, filtered to apartments, hotels, B&Bs, guest houses and holiday homes, review score 7+. Booking's own score filter is coarse (a 7.4 slipped through in testing), so the 7.5 rule is enforced in Python.
3. For the cheapest few plausible cards, open the property page for the exact dates and parse:
   * JSON-LD: name, `aggregateRating` (score + review count), address;
   * `accommodation_type_id` (e.g. 203 = Hostel → excluded);
   * per-rate JSON (`b_rooms_available_and_soldout`): max persons, total price incl. excluded charges (city tax, cleaning fee), cancellation type;
   * the room table: **room-level** facilities ("Private bathroom", "Private external bathroom", "Ensuite bathroom", "Shared bathroom"), cancellation and prepayment text.
4. Confirm that the page's check-in/check-out equal the requested dates.

If a CAPTCHA appears, `BOOKING_WINDOW=visible` waits up to 90 s for the user to solve it; off-screen and headless runs stop with a "blocked" status. Booking.com's markup changes often; parsing failures surface as provider errors, never as invented offers.

## What works with only an OpenAI key

* Travel Coordinator (natural language → constraints), Flight Agent orchestration, Accommodation Agent explanations, Itinerary Agent.
* Full demo workflow on the clearly labelled DEMO dataset.
* Manual import of flights/accommodation (with optional AI extraction from pasted text, which the user reviews).
* Itineraries with OpenStreetMap facts; provider deep links; FX conversion.

## What needs opt-in configuration

* `WIZZ_LIVE_ENABLED=true`: live Wizz Air fares (no extra credentials).
* `BOOKING_SCRAPER_ENABLED=true` + `python -m playwright install chromium`: live Booking.com rooms (browser window off-screen by default).

## What remains unavailable

* Official Wizz Air API (does not exist); Booking.com Demand API (contract).
* Wizz Air flight numbers, exact arrival times, bag and seat prices, bundle prices (bot-protected).
* Booking.com availability for multiple *different* room types combined into one stay (only N× the same room type is modelled).
* Any booking or payment: TravelScout only links to the provider.
