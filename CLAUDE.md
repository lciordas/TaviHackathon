# Tavi (Hackathon Build)

AI-native managed marketplace for blue-collar trades. Facility managers submit work orders; the system discovers vendors, runs a multi-modal agentic auction, and dispatches the winning quote.

One repo, one project, three subparts — each developed in its own Claude Code session for context isolation. Everything lives together in this directory.

1. **Intake** (`backend/`) — chat → structured work order. First cut scaffolded.
2. **Vendor discovery** (`vendor-discovery/`) — work order → ranked nearby vendors. Currently in progress.
3. **Vendor contact / auctioning** — agentic outreach + state machine + command center. Not yet started.

## Hackathon context

- ~48-hour build, 60-min mid-build co-work + end-of-build demo
- Evaluated on: technical aptitude, hustle, taste
- Subpart 3 (vendor contact / auctioning) is where most engineering time should land per the rubric
- Submission: repo link + setup/run instructions + exported prompt history

## Stack

- Backend: FastAPI (Python)
- Frontend: Next.js (React, TypeScript)
- DB: SQLite via SQLAlchemy (swap-ready to Postgres; their internal stack is Postgres)
- LLM: Anthropic Claude via `anthropic` SDK
- External APIs: Google Places (intake geocoding + autocomplete); vendor discovery uses the same response shape in v0 but against fixtures, not live calls
- Deferred: Twilio, Resend, voice providers

## Architecture (shared across subparts)

Three layers, same pattern across all three subparts:

1. **Frontend** — Next.js + React + TypeScript. Subpart-specific UIs (intake chat, command center) share one app. Hosted privately (hackathon v0: localhost dev / unlisted deploy). Communicates with the backend over HTTPS / JSON (REST).

2. **Backend** — FastAPI (Python). The only process that touches the DB and external APIs. Exposes REST endpoints to the frontend.

3. **Data & external services** (reached only through the backend):
   - **SQLite** via SQLAlchemy — primary store
   - **Anthropic Claude** — LLM for field extraction, reply drafting, vendor persona simulation
   - **Google Places** — address geocoding + autocomplete (intake); vendor discovery v0 uses fixtures with the same response shape
   - Additional external APIs to be specified later

Key principle: the frontend never talks directly to external APIs or the DB. All third-party credentials and DB access live in the backend.

## Subpart 1 — Intake (`backend/`)

Captures a facility manager's service request and persists it as a structured work order.

### Data model

Single table: `work_orders`. Fields mirror what a customer provides during intake.

- `id`, `created_at`, `created_by`
- `trade` — enum: plumbing, hvac, electrical, lawncare, handyman, appliance_repair
- `description` — free text
- `address_line`, `city`, `state`, `zip`, `lat`, `lng`
- `access_notes` — nullable free text
- `urgency` — enum: emergency, urgent, scheduled, flexible
- `scheduled_for` — datetime (UTC)
- `budget_cap_cents` — int
- `quality_threshold` — float, nullable (min vendor composite score the customer will accept)
- `requires_licensed` — bool
- `requires_insured` — bool

### Data flow

- User types in chat → frontend posts to FastAPI → FastAPI calls Claude to extract / confirm fields → FastAPI writes a `work_orders` row → response back to UI
- User types address → frontend queries FastAPI proxy → FastAPI calls Google Places → autocomplete suggestions + geocoded lat/lng back to UI

### Scope

**In:** chat intake (LLM extracts → user confirms → persists), geocoding on submission.
**Out:** anything about vendors, engagements, messages, voice intake, auth / multi-tenancy / payments — those belong to subparts 2 and 3.

### Conventions (subpart 1)

- One table, one purpose: rows represent submitted work orders
- Every row is a complete submitted order — no draft persistence (chat state is ephemeral)
- Real geocoding pipeline even if dev data is local

## Subpart 2 — Vendor discovery (`vendor-discovery/`)

Given a submitted work order, discover candidate vendors within ~20 miles, score them across multiple signals (Google rating, review count, BBB grade, license status, insurance, years in business, etc.), and return a ranked list.

### Inputs / outputs

- **Input**: a `work_order` row produced by subpart 1.
- **Output**: a ranked vendor list with composite score + per-signal breakdown.

### Data sources

In production, aggregates 21+ public sources (Google Places, BBB, state license boards, etc.). For v0, seeded fixture data at `vendor-discovery/data/seed/` mimics what those APIs return:

- `requests.json` — submitted work orders + the chat messages that produced them
- `places.json` — Google Places-shaped nearby-vendor results, extended with BBB / license / insurance / years-in-business signals so scoring has real differentiation
- `generate_places.py` — the generator that produced `places.json` (reproducible from `requests.json` with a fixed seed)

### Scope

**In:** real composite scoring + radius / trade filter over seeded data; production-shaped pipeline (real shape even when data is fixture).
**Out:** live vendor scraping, paid API calls — fixture only for v0.

## Subpart 3 — Vendor contact / auctioning (planned)

Agentic outreach across email / SMS / phone, unified per-engagement thread, kanban command center keyed to an engagement state machine (`prospecting → contacted → quoted → negotiating → dispatched → completed`, with `declined` / `ghosted` off-ramps). LLM-simulated vendor personas auto-respond when messaged. Not yet started.

## Conventions (project-wide)

- Breadth first across all three subparts, then deepen subpart 3
- Real pipelines, seeded inputs: scoring / aggregation logic is production-shaped even when data is fixture
- Human-in-the-loop: LLM drafts, human approves before any state-changing action
- One unified thread per engagement across modalities — no per-modality silos in the UI
- Engagement state machine is the central abstraction; UI surfaces it directly
- All datetimes in UTC

## Commands

TBD — populated once scaffolding is in.
