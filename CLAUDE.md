# Tavi (Hackathon Build)

AI-native managed marketplace for blue-collar trades. Facility managers submit work orders; the system discovers vendors, runs a multi-modal agentic auction, and dispatches the winning quote.

One repo, one project, three subparts — each developed in its own Claude Code session for context isolation. Everything lives together in this directory.

1. **Intake** (`backend/` + `frontend/`) — chat → structured work order. Done: chat intake, Google Places autocomplete for service address, SQLite persistence. On confirm, intake automatically kicks off subpart 2 as a background task.
2. **Vendor discovery** (`backend/app/services/discovery/` + `backend/app/routers/discovery.py`) — work order → ranked nearby vendors. Done: live Google Places (new API) nearby search, BBB profile scrape, cumulative + urgency-aware subjective scoring, hard filters (distance / hours / quality threshold), `/discovery/run` endpoint, admin DB explorer. `vendor-discovery/` (top-level dir) holds archival fixture data from an earlier spike — not load-bearing.
3. **Vendor contact / auctioning** — agentic outreach + engagement state machine + command center. Schema placeholders (`Negotiation.messages`, `actions_log`, `status` keyed to `EngagementStatus`) are already in the DB. Outreach / UI not yet started.

## Hackathon context

- ~48-hour build, 60-min mid-build co-work + end-of-build demo
- Evaluated on: technical aptitude, hustle, taste
- Subpart 3 (vendor contact / auctioning) is where most engineering time should land per the rubric
- Submission: repo link + setup/run instructions + exported prompt history

## Stack

- Backend: FastAPI (Python, `uv` for deps)
- Frontend: Next.js 16 / React 19 / TypeScript / Tailwind v4
- DB: SQLite via SQLAlchemy (swap-ready to Postgres; their internal stack is Postgres)
- LLM: Anthropic Claude via `anthropic` SDK (model: `claude-haiku-4-5-20251001` by default)
- External APIs:
  - **Google Places API (new)** — address autocomplete (intake) + nearby/text search + place details (discovery). Requires `GOOGLE_PLACES_API_KEY`.
  - **BBB (bbb.org)** — scraped via `httpx` + `beautifulsoup4` for grade, accreditation, complaint counts, years-in-business.
- Deferred: Twilio, Resend, voice providers

## Architecture (shared across subparts)

Three layers, same pattern across all three subparts:

1. **Frontend** — Next.js + React + TypeScript. Subpart-specific UIs share one app: `frontend/app/page.tsx` is the intake chat + address picker; `frontend/app/admin/page.tsx` is a read-only DB explorer used to inspect discovery output end-to-end. Hosted privately (hackathon v0: localhost dev / unlisted deploy). Communicates with the backend over HTTPS / JSON (REST).

2. **Backend** — FastAPI (Python). The only process that touches the DB and external APIs. Router layout under `backend/app/routers/`:
   - `/intake/*` — chat turn, start, confirm (intake.py)
   - `/intake/places/*` — Google Places autocomplete proxy (places.py)
   - `/discovery/*` — vendor discovery runs (discovery.py)
   - `/admin/*` — read-only DB views for the admin explorer (admin.py)
   - `/health` — liveness

3. **Data & external services** (reached only through the backend):
   - **SQLite** via SQLAlchemy — primary store (file: `backend/tavi.db`)
   - **Anthropic Claude** — field extraction + reply drafting (intake); will drive outreach drafting + vendor persona simulation in subpart 3
   - **Google Places API (new)** — autocomplete + place details (intake); nearby/text search + place details (discovery)
   - **BBB scrape** — enriches each vendor with grade / accreditation / complaint resolution / tenure for scoring

Key principle: the frontend never talks directly to external APIs or the DB. All third-party credentials and DB access live in the backend.

## Subpart 1 — Intake (`backend/` + `frontend/app/page.tsx`)

Captures a facility manager's service request and persists it as a structured work order. Backend code lives in `backend/app/routers/intake.py`, `backend/app/routers/places.py`, and `backend/app/services/intake.py`; the LLM plumbing is in `backend/app/agent.py` + `backend/app/prompts.py` + `backend/app/tools.py`.

### Data model (`work_orders` table)

- `id`, `created_at`, `created_by`
- `trade` — enum: plumbing, hvac, electrical, lawncare, handyman, appliance_repair
- `description` — free text
- `address_line`, `city`, `state`, `zip`, `lat`, `lng` — **required**, populated by the frontend's Google Places autocomplete widget (not by the LLM)
- `access_notes` — nullable free text
- `urgency` — enum: emergency, urgent, scheduled, flexible
- `scheduled_for` — datetime (UTC)
- `budget_cap_cents` — int
- `quality_threshold` — float; required at collection time (LLM always confirms a value, defaults to 4.0 when the user waffles)
- `requires_licensed`, `requires_insured` — bools

### Data flow

- **Chat turn**: user types → `POST /intake/chat` → FastAPI runs a Claude tool-use loop that may call `update_fields` → returns the updated `WorkOrderPartial` + reply + `is_ready` + `missing` list. LLM does NOT set address fields directly; if it notices an address in chat it can set `address_hint` (transient, non-persisted) to seed the UI's autocomplete input.
- **Address picker**: user types in the autocomplete input → `POST /intake/places/autocomplete` → backend hits Google Places → suggestions back. User clicks one → `POST /intake/places/select` → backend hits Places `:getPlace` → structured `{address_line, city, state, zip, lat, lng, formatted_address}` back. The frontend patches these into `fields` locally; they ride along in the next `/intake/chat` or `/intake/confirm` request.
- **Confirm**: once all required fields are non-null and the user affirms, `POST /intake/confirm` persists the `work_orders` row and **spawns a background task that calls `run_discovery`** (subpart 2). The confirm response returns immediately with the work-order ID; discovery completes asynchronously.

### Scope

**In:** chat intake (LLM extracts → user confirms → persists), Google Places autocomplete, automatic hand-off to subpart 2.
**Out:** voice intake, auth / multi-tenancy / payments — those remain deferred.

### Conventions (subpart 1)

- No draft persistence: chat state is ephemeral, only confirmed orders hit the DB
- Address fields are NOT set by the LLM — only by the UI's Places picker
- User profile is intentionally blank in v0 (no saved defaults); every conversation starts fresh

## Subpart 2 — Vendor discovery (`backend/app/services/discovery/`)

Given a submitted work order, discover candidate vendors within ~20 miles using live Google Places, enrich with a BBB scrape, compute objective + urgency-aware subjective scores, apply hard filters, rank, and persist. The result is inspectable via the admin DB explorer.

### Inputs / outputs

- **Input**: a `work_order` row produced by subpart 1 (must have `lat`/`lng`).
- **Output**: a `DiscoveryRun` row + a `Negotiation` row per candidate vendor, plus cached `Vendor` rows. `DiscoveryRunResponse` schema bundles these into `ranked` + `filtered` lists.

### Data sources (live, not fixtures)

- **Google Places API (new)** via `places_client.py` — `searchNearby` for plumbing/electrical (those have first-class type tags), `searchText` for hvac/handyman/lawncare/appliance_repair (the new API rejects e.g. `hvac_contractor` outright). Trade → strategy mapping in `trade_map.py`. Uses IDs-only masks for search (free tier) and an Enterprise field mask for details.
- **BBB scrape** via `bbb_client.py` — `httpx` + `beautifulsoup4`, rate-limited by `BBB_REQUEST_DELAY_S`. Best-effort: BBB failures never crash discovery.
- `vendor-discovery/data/seed/` (top-level dir) is archival fixture data from an earlier spike — not consumed by the live pipeline.

### Data model (additional tables)

- `vendors` — cache keyed by Google `place_id`. Holds Google fields (display_name, rating, review count, hours, phone, website, 24/7 flag), BBB fields (grade, accreditation, complaints, years-in-business), and the computed `cumulative_score` + breakdown. Re-fetched only when stale.
- `discovery_runs` — one row per `/discovery/run` invocation. Audit + cost tracking: candidate count, cache hits, API detail calls, BBB scrape count, duration, weight profile (urgency).
- `negotiations` — one row per (work_order × vendor). Holds filter state, `quote_cents` (filled by subpart 3 when a vendor responds), `subjective_rank_score` + `rank` (also subpart-3 territory), engagement `status`, and placeholder `messages` / `actions_log` JSON columns.

### Scoring (`scoring.py`)

Two scores with very different lifecycles:

- **`cumulative_score`** (objective, on `Vendor`) — **runs at discovery time.** Bayesian-adjusted Google rating (45%) + BBB grade (25%) + complaint resolution rate (10%) + tenure (20%). Missing signals drop out and remaining weights renormalize. Stable per vendor across customers.
- **`subjective_rank_score`** (per-order, on `Negotiation`) — **does NOT run at discovery time.** Requires `Negotiation.quote_cents`, which only exists after subpart 3's outreach agent has contacted a vendor and received a price. Computed by `compute_subjective(cumulative_score, quote_cents, budget_cap_cents, weights: RankingWeights)`. Leaves a Negotiation's rank / subjective score null until a quote is in.

### Filters (`filters.py`)

Hard filters applied before ranking: business status != operational, distance > 20mi, hours overlap with `scheduled_for`, `bayes_rating < quality_threshold`. Licensed / insured flags are **not** enforced here — those checks are deferred to subpart 3 (ask the vendor directly).

### Entrypoints

- `POST /discovery/run` — manual trigger (takes `work_order_id`, `refresh` flag). Idempotent within a 24h window per work order.
- `GET /discovery/run/{run_id}` — hydrated view of one run.
- Background invocation from `/intake/confirm` — failures log + swallow (never block intake).

### Conventions (subpart 2)

- Real pipeline on real APIs: no fixtures in the live path
- In-DB vendor cache keeps Places bill flat across repeated discovery runs
- BBB enrichment is best-effort; vendors without BBB profiles still score (cumulative weights renormalize)
- `quality_threshold` is checked against the Bayesian-adjusted rating, not raw Google stars
- Discovery does NOT rank — survivors land at `prospecting` with null rank / subjective score. Ranking is a subpart-3 job once vendors quote.

## Subpart 3 — Vendor contact / auctioning (planned)

Agentic outreach across email / SMS / phone, unified per-engagement thread, kanban command center keyed to an engagement state machine (`prospecting → contacted → quoted → negotiating → dispatched → completed`, with `declined` / `ghosted` off-ramps). LLM-simulated vendor personas auto-respond when messaged. Outreach + UI not yet started, but the DB + scoring helpers are already wired:

- `EngagementStatus` enum in `backend/app/enums.py`
- `Negotiation.status` defaults to `PROSPECTING` at discovery time
- `Negotiation.quote_cents` (int, null until quote arrives) — input to the subjective-ranking formula
- `Negotiation.messages` (JSON) + `Negotiation.actions_log` (JSON) are pre-allocated for outreach history + discrete actions
- `scoring.compute_subjective(cumulative_score, quote_cents, budget_cap_cents, weights)` → quote-aware rank score (quality * w_quality + price_fit * w_price)
- `scoring.RankingWeights(quality, price)` — two-axis weight profile (sums to 1.0); a `speed` axis lands when vendor-proposed schedules arrive
- `scoring.default_weights_for(urgency)` — starting profile keyed to `WorkOrder.urgency`. Emergency leans heavily on quality (0.80 / 0.20); flexible leans on price (0.35 / 0.65)
- `scoring.PRESET_WEIGHTS` — named profiles for the FM-override UI (`balanced`, `quality_leaning`, `price_leaning`, `quality_only`, `price_only`)

**Planned ranking UX**: once every `prospecting` negotiation moves to `quoted` (has `quote_cents`), subpart 3 computes subjective rank using the work order's default weights and surfaces the ranked list to the facility manager. The UI explains what the ranking is optimizing for ("You asked for it urgent, so I leaned toward quality") and offers one-click re-rank using the `PRESET_WEIGHTS` — no new vendor interaction needed, just a different weights arg passed to `compute_subjective`.

## Admin DB explorer (`frontend/app/admin/page.tsx`)

Read-only surface for inspecting the pipeline end-to-end during the demo. Lists work orders, cached vendors (with cumulative score breakdowns), discovery runs (with audit counts), and negotiations (joined with vendor display name, showing subjective rank breakdown + filter reasons). Backed by `/admin/*` endpoints in `backend/app/routers/admin.py`. Linked from the intake page header and from the post-submit confirmation.

## Conventions (project-wide)

- Breadth first across all three subparts, then deepen subpart 3
- Real pipelines, real APIs: subpart 2 runs against live Google Places + BBB, not fixtures
- Human-in-the-loop: LLM drafts, human approves before any state-changing action
- One unified thread per engagement across modalities — no per-modality silos in the UI
- Engagement state machine is the central abstraction; UI surfaces it directly
- All datetimes in UTC

## Commands

### Backend (`backend/`)

- Install deps: `uv sync`
- Initialize SQLite schema: `uv run python create_db.py`
- Run server: `uv run uvicorn app.main:app --reload --port 8000`
- Interactive chat REPL (talks to the running server): `uv run python chat.py`
- Unit tests: `uv run pytest` — covers hours-overlap edge cases (cross-midnight / 24/7 / missing hours), scoring math (Bayesian anchor, urgency weight profiles, BBB-missing reweight), and the BBB HTML parser against inline fixtures

### Frontend (`frontend/`)

- Install deps: `npm install`
- Dev server: `npm run dev` (defaults to `http://localhost:3000`)
- Production build: `npm run build && npm run start`
- Lint: `npm run lint`

### Environment (`backend/.env`)

- `ANTHROPIC_API_KEY` — required
- `ANTHROPIC_MODEL` — defaults to `claude-haiku-4-5-20251001`
- `CORS_ORIGINS` — JSON array, defaults to `["http://localhost:3000"]`
- `GOOGLE_PLACES_API_KEY` — required for `/intake/places/*` and `/discovery/run`
- `GOOGLE_PLACES_DEFAULT_RADIUS_M` — defaults to 32186 (~20mi)
