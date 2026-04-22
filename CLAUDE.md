# Tavi (Hackathon Build)

AI-native managed marketplace for blue-collar trades. Facility managers submit work orders; the system discovers vendors, runs a multi-modal agentic auction, and dispatches the winning quote.

One repo, one project, three subparts — each developed in its own Claude Code session for context isolation. Everything lives together in this directory.

1. **Intake** (`backend/` + `frontend/`) — chat → structured work order. Done: chat intake, Google Places autocomplete for service address, SQLite persistence. On confirm, intake automatically kicks off subpart 2 as a background task.
2. **Vendor discovery** (`backend/app/services/discovery/` + `backend/app/routers/discovery.py`) — work order → ranked nearby vendors. Done: live Google Places (new API) nearby search, BBB profile scrape, cumulative + urgency-aware subjective scoring, hard filters (distance / hours / quality threshold), `/discovery/run` endpoint, admin DB explorer. `vendor-discovery/` (top-level dir) holds archival fixture data from an earlier spike — not load-bearing.
3. **Vendor contact / auctioning** (`backend/app/services/negotiation/` + `frontend/app/work-orders/[id]/page.tsx`) — agentic outreach + 9-state machine + tick-driven scheduler + command-center UI. Done: LLM-backed Tavi Coordinator with 8-tool surface; simulated vendor agents with persona trait packs; scheduler with per-persona skip rolls, ghoster + refusal behaviors, silence timeouts; sequential booking flow with credential verification → confirmation → accept/decline; cached pitch template (one LLM call reused across every vendor's opening); mid-tick DB commits so the command-center UI streams messages in real time.

## Hackathon context

- ~48-hour build, 60-min mid-build co-work + end-of-build demo
- Evaluated on: technical aptitude, hustle, taste
- Subpart 3 (vendor contact / auctioning) is where most engineering time should land per the rubric
- Submission: repo link + setup/run instructions + exported prompt history

## Stack

- Backend: FastAPI (Python, `uv` for deps)
- Frontend: Next.js 16 / React 19 / TypeScript / Tailwind v4
- DB: SQLite via SQLAlchemy (swap-ready to Postgres; their internal stack is Postgres)
- LLM: Anthropic Claude via `anthropic` SDK (model: `claude-sonnet-4-6` by default)
- External APIs:
  - **Google Places API (new)** — address autocomplete (intake) + nearby/text search + place details (discovery). Requires `GOOGLE_PLACES_API_KEY`.
  - **BBB (bbb.org)** — scraped via `httpx` + `beautifulsoup4` for grade, accreditation, complaint counts, years-in-business.
  - **MailPit** — local SMTP + HTTP email bus between Tavi and the simulated vendors. Run separately (`mailpit` binary, SMTP on 1025, UI on 8025). Used only by subpart 3.
- Deferred: Twilio, Resend, voice providers

## Architecture (shared across subparts)

Three layers, same pattern across all three subparts:

1. **Frontend** — Next.js + React + TypeScript. Subpart-specific UIs share one app: `frontend/app/page.tsx` is the intake chat + address picker; `frontend/app/admin/page.tsx` is a read-only DB explorer used to inspect discovery output end-to-end. Hosted privately (hackathon v0: localhost dev / unlisted deploy). Communicates with the backend over HTTPS / JSON (REST).

2. **Backend** — FastAPI (Python). The only process that touches the DB and external APIs. Router layout under `backend/app/routers/`:
   - `/intake/*` — chat turn, start, confirm (intake.py)
   - `/intake/places/*` — Google Places autocomplete proxy (places.py)
   - `/discovery/*` — vendor discovery runs (discovery.py)
   - `/negotiations/*` — scheduler tick + per-work-order hydration for the command center (negotiations.py)
   - `/admin/*` — read-only DB views for the admin explorer (admin.py)
   - `/health` — liveness

3. **Data & external services** (reached only through the backend):
   - **SQLite** via SQLAlchemy — primary store (file: `backend/tavi.db`)
   - **Anthropic Claude** — field extraction + reply drafting (intake); outreach drafting, cached pitch templating, vendor persona simulation, and per-turn booking / verification logic (subpart 3)
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
- `negotiations` — one row per (work_order × vendor). Discovery seeds it with `filtered` + `filter_reasons`; subpart 3 advances `state` (NegotiationState enum), fills `quoted_price_cents` + `quoted_available_at` when the vendor commits, writes `subjective_rank_score` + `rank` after ranking, and stashes freeform extracted facts (insurance/license verification, terminal reasons, ghoster/refusal flags, booking-confirmation timestamps) into the `attributes` JSON bag.
- `negotiation_messages` — message thread per negotiation. One row per outbound Tavi message and per vendor reply, with `sender` (tavi/vendor), `channel` (email/sms/phone), `iteration` (the scheduler-tick on which it was written), and JSON `content` (`{text, subject?}`).

### Scoring (`scoring.py`)

Two scores with very different lifecycles:

- **`cumulative_score`** (objective, on `Vendor`) — **runs at discovery time.** Bayesian-adjusted Google rating (45%) + BBB grade (25%) + complaint resolution rate (10%) + tenure (20%). Missing signals drop out and remaining weights renormalize. Stable per vendor across customers.
- **`subjective_rank_score`** (per-order, on `Negotiation`) — **does NOT run at discovery time.** Requires `Negotiation.quoted_price_cents`, which only exists after subpart 3's outreach agent has contacted a vendor and received firm terms. Computed by `compute_subjective(cumulative_score, quote_cents, budget_cap_cents, weights: RankingWeights)` (the `quote_cents` parameter is passed `neg.quoted_price_cents`). Refreshed at the top of every tick so the command center shows a live leaderboard as quotes arrive.

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

## Subpart 3 — Vendor contact / auctioning (`backend/app/services/negotiation/` + `frontend/app/work-orders/[id]/page.tsx`)

Fully autonomous agentic auction. A single tick button in the command center advances the scheduler by one iteration: Tavi pitches pending prospects, vendors reply (or skip, or refuse, or ghost), quotes land, credentials get verified, the top-ranked vendor gets booked, the rest get auto-declined. All driven by two LLM agents behind a state machine.

### State machine (`NegotiationState` in `backend/app/enums.py`)

Nine states. Five active, four terminal (write-once):

```
PROSPECTING → CONTACTED → NEGOTIATING → QUOTED → SCHEDULED → COMPLETED
                                         ↓
                                         DECLINED / NOSHOW / CANCELLED
```

- `PROSPECTING`: work order just created; Tavi hasn't reached out yet. First tick sends the opening pitch and transitions to `CONTACTED`.
- `CONTACTED`: pitch sent; awaiting vendor's first reply. Vendor may reply normally, refuse politely (direct state jump to `DECLINED`), or go silent.
- `NEGOTIATING`: active dialogue. Tavi pushes for firm terms + extracts facts. `record_quote` transitions to `QUOTED`.
- `QUOTED`: firm price + date on file. Waits silently until `ready_to_schedule` flips; then enters the booking sub-flow (see below).
- `SCHEDULED`: winner locked in. Awaits operator signal for `COMPLETED` / `NOSHOW` (not yet wired; they're still planned external controls).
- `COMPLETED` / `NOSHOW` / `DECLINED` / `CANCELLED`: terminal.

### Time as loop iterations

Wall clock is irrelevant in the demo — time is measured in scheduler ticks. `WorkOrder.loop_iteration` bumps on every tick. `negotiation_messages.iteration` records which tick each message was written on, so the UI can render "vendor went cold for 3 ticks" gaps between messages. Timeouts are iteration-denominated:

- `SILENCE_TIMEOUT_TICKS = 3`: vendor silent through the pre-quote funnel → scheduler force-declines.
- `CONFIRMATION_TIMEOUT_TICKS = 2`: vendor silent after a booking-confirmation request → force-decline, next rank takes over.

### Scheduler top loop (`scheduler.py`)

One public entry: `tick(db, work_order_id)`. Each call:

1. Increments `WorkOrder.loop_iteration` and commits (UI sees it immediately).
2. Refreshes `subjective_rank_score` + `rank` across every currently-QUOTED neg using `scoring.default_weights_for(urgency)`.
3. Determines the "active pick" for the booking phase: lowest-rank QUOTED neg when `ready_to_schedule=true`.
4. Walks every non-filtered active negotiation, dispatches per-state, commits after each (UI streams messages in real time).
5. Cascade-declines remaining QUOTED peers if anyone hit `SCHEDULED` this tick.
6. Recomputes `ready_to_schedule` + commits.

### `ready_to_schedule` gate (`readiness.py`)

Monotonic `WorkOrder` flag. Flips `true` the moment every non-filtered negotiation lands in `{QUOTED, SCHEDULED, COMPLETED, NOSHOW, DECLINED, CANCELLED}` — i.e., no one is still actively pre-quote. Checked after every successful coordinator tool call and at end-of-tick. Once true, never flips back (transitions out of the ready-set don't exist in the state machine).

### Agents

**Tavi Coordinator** (`coordinator.py` + `prompts.py` + `tools.py`). Stateless Anthropic call per turn. 8 tools:

| Tool | Effect |
|------|--------|
| `send_email` / `send_sms` / `send_phone` | Appends an outbound message; transitions `PROSPECTING → CONTACTED` on first send |
| `record_quote` | Sets `quoted_price_cents` + `quoted_available_at`; transitions `NEGOTIATING → QUOTED` |
| `record_facts` | Merges freeform key/values into `Negotiation.attributes` (license_verified, insurance_carrier, etc.) |
| `close_negotiation` | `CONTACTED` / `NEGOTIATING` → `DECLINED` with a terminal reason (pre-quote walkaway) |
| `accept_quote` | `QUOTED → SCHEDULED` |
| `decline_quote` | `QUOTED → DECLINED` |

No `counter_quote`, no `escalate` — v0 runs fully autonomously with no human review path.

**Vendor simulator** (`simulator.py`). Stateless Anthropic call returning one plain-text message. Sees the thread flipped to vendor-perspective, their own persona markdown, the work order, and the channel of Tavi's last message. No tools.

### Vendor personas (`backend/app/personas/pool/`)

8 markdown persona archetypes (`01_ace_premium.md` … `08_family_shop.md`) spanning the trait axes called out in `docs/vendor-simulator.md` — price orientation, negotiability, responsiveness, pickiness, reliability signals, tone. Randomly assigned in `cache.upsert_google` on first vendor cache, then stable across re-discoveries. Full markdown is copied onto `Vendor.persona_markdown`.

Assignment-time also synthesizes a fake contact email (`contact@{slug}.example` on `Vendor.email`) so the email-first channel-selection rule fires in the demo — Google Places doesn't expose contact email.

### Vendor first-reply behaviors

Three mutually exclusive outcomes on the first vendor-turn in CONTACTED (checked in order):

1. **Ghoster** (inverse-weighted to quality; 5–35% band). Vendor never replies. Caches `attributes.is_ghoster=true`; every subsequent tick skips. Silence timeout eventually terminates them.
2. **Refusal** (positive-weighted to quality; 5–15% band). Vendor posts one polite decline from a 5-message pool; state jumps directly to `DECLINED` with `terminal_reason: "vendor declined the opportunity"`.
3. **Engage**. Normal dialogue. `attributes.refused=false` cached to prevent re-rolls.

Persona `responsiveness` trait governs the per-tick skip probability on top of ghoster (`prompt`=10%, `terse`=20%, `slow`=60%).

### Sequential booking flow (post-`ready_to_schedule`)

All-vendors-queued, one-at-a-time. The rank-1 QUOTED neg is the "active pick"; every other QUOTED neg is silent until it resolves. On the active pick:

1. **Credential verification** (if `WorkOrder.requires_licensed` / `requires_insured` and the creds aren't on file yet):
   - `quote_action=verify_credentials` — Tavi asks directly about the missing creds.
   - Vendor reply → `quote_action=process_verification` — coordinator calls `record_facts` (positive answer), sends a follow-up (ambiguous), or calls `decline_quote` (refused / can't provide).
   - Silence ≥ `SILENCE_TIMEOUT_TICKS` → scheduler force-declines; next rank takes over.
2. **Booking confirmation** (once credentials are verified):
   - `quote_action=request_confirmation` — Tavi asks the vendor to confirm they're locked in at the quoted terms.
   - Vendor reply → `quote_action=respond_to_confirmation` — coordinator calls `accept_quote` (→ SCHEDULED + cascade-decline peers) or `decline_quote`.
   - Silence ≥ `CONFIRMATION_TIMEOUT_TICKS` → force-decline; next rank takes over.

### Email bus — MailPit (`mailpit.py` + `inbound.py`)

Tavi ↔ vendor communication rides on a real email bus. MailPit catches all mail locally (SMTP 1025, HTTP API 8025).

- **Tavi outbound** (`tools._send` → `mailpit.send_tavi_to_vendor`): every `send_email` tool call SMTP-sends to MailPit `From: tavi+{work_order_id}@tavi.local` `To: {vendor.email}`. DB write happens alongside (DB remains the canonical thread for the UI).
- **Vendor outbound** (`simulator.run_turn` → `mailpit.send_vendor_to_tavi`): simulator SMTP-sends the reply; does NOT write to DB.
- **Simulator's thread view** (`mailpit.fetch_vendor_thread`): pulled via MailPit's HTTP search API using `addressed:{vendor_email}` (matches both to and from). The simulator has no direct DB access — its entire view of the negotiation comes through MailPit.
- **Inbound sweep** (`inbound.sweep`, end of each tick): polls MailPit for unread messages to `tavi+{work_order_id}@...`, matches From address to a `Vendor.email`, writes each as a `NegotiationMessage` with `sender=VENDOR`, and marks the MailPit message read.
- **Fallback**: if MailPit is down, coordinator writes DB directly (no SMTP), simulator reads/writes DB directly (legacy path). Negotiation still progresses; inbound sweep is a no-op.

### Pitch-template caching (`pitch.py`)

One Anthropic call per work order generates a shared opening pitch with `{{vendor_name}}` placeholder, cached on `WorkOrder.pitch_template` (JSON `{subject, body}`). Every subsequent vendor's PROSPECTING turn skips the coordinator LLM loop, substitutes the vendor name, and dispatches `send_email` directly — ~87% fewer opening Anthropic calls on a 12-vendor run.

### Discovery candidate cap

`MAX_CANDIDATES = 12` in `orchestrator.py` — keeps the auction demoable within a handful of ticks. Google Places allows up to 20 per page; we take the top N by search relevance.

### Command center (`frontend/app/work-orders/[id]/page.tsx`)

Per-work-order screen. Header shows the work order summary, iteration counter, prominent Tick button, and a green "ready to schedule" pill once the flag flips. Kanban by state (5 active columns), concluded bucket collapsible below, excluded-at-discovery bucket below that. Clicking a card opens a thread panel with full message history (rendering "— N ticks of silence —" separators between messages), firm quote, extracted facts.

While the Tick POST is in flight, the UI polls `/negotiations/by_work_order` every 400ms — since the scheduler commits after each per-neg dispatch, messages stream into the board as they're written.

Tick banner summarizes the tick: messages sent, silent, timed out (red), refused (amber), plus callouts for "Verifying credentials → Vendor", "Confirmation request → Vendor", and "Booking confirmed — Vendor".

### Conventions (subpart 3)

- Time is measured in ticks, not seconds; every delay is iteration-denominated.
- State column is the source of truth — what Tavi says in a message is LLM output; what the kanban shows is the DB.
- Fully autonomous. No human-in-the-loop approval flow in v0; operator-driven `COMPLETED` / `NOSHOW` / `CANCELLED` buttons are still deferred.
- Message thread is the shared communication layer between Tavi and the vendor simulator — future email/SMS/phone integrations layer on top of the same table.

## Admin DB explorer (`frontend/app/admin/page.tsx`)

Read-only surface for inspecting the pipeline end-to-end during the demo. Lists work orders, cached vendors (with cumulative score breakdowns + persona markdown previews), discovery runs (with audit counts), and negotiations (joined with vendor display name, showing subjective rank, filter reasons, quote + availability, extracted attributes, and the full message thread). Backed by `/admin/*` endpoints in `backend/app/routers/admin.py`. Linked from the intake page header and the post-submit confirmation.

The command center at `/work-orders/[id]` is the live, interactive view — admin is for debugging the raw state.

## Conventions (project-wide)

- Real pipelines, real APIs: subpart 2 runs against live Google Places + BBB, not fixtures; subpart 3 runs against live Anthropic.
- State machine is the source of truth. The LLM drafts messages; the DB reflects reality. What the kanban shows is what actually happened.
- One unified thread per engagement across modalities — no per-modality silos in the UI. Email/SMS/phone tool calls all land in the same `negotiation_messages` table with a `channel` tag.
- Fully autonomous in v0 — no human-in-the-loop approval. Planned later: operator-driven `COMPLETED` / `NOSHOW` / `CANCELLED` buttons from `SCHEDULED`.
- Time in subpart 3 is measured in scheduler ticks, not wall-clock seconds.
- All DB datetimes in UTC.

## Commands

### Backend (`backend/`)

- Install deps: `uv sync`
- Initialize SQLite schema: `uv run python create_db.py`
- Run server: `uv run uvicorn app.main:app --reload --port 8000`
- Interactive chat REPL (talks to the running server): `uv run python chat.py`
- Start MailPit (subpart 3 email bus; separate terminal): `mailpit`
  — SMTP on `localhost:1025`, UI + HTTP API on `http://localhost:8025`. If MailPit isn't running, the negotiation subsystem falls back to direct DB writes and a warning in the backend logs.
- Unit tests: `uv run pytest` — ~80 tests across: hours-overlap edge cases (cross-midnight / 24/7 / missing hours), scoring math (Bayesian anchor, urgency weight profiles, BBB-missing reweight, quote-aware subjective ranking), BBB HTML parser fixtures, coordinator tool dispatchers (state guards, attribute merges), pitch-template substitution, and the full scheduler flow (turn resolution, ghoster + refusal rolls, silence/confirmation/verification timeouts, sequential booking flow, cascade decline, readiness monotonicity). Stubs the LLM agents; no Anthropic calls during `pytest`

### Frontend (`frontend/`)

- Install deps: `npm install`
- Dev server: `npm run dev` (defaults to `http://localhost:3000`)
- Production build: `npm run build && npm run start`
- Lint: `npm run lint`

### Environment (`backend/.env`)

- `ANTHROPIC_API_KEY` — required
- `ANTHROPIC_MODEL` — defaults to `claude-sonnet-4-6`
- `CORS_ORIGINS` — JSON array, defaults to `["http://localhost:3000"]`
- `GOOGLE_PLACES_API_KEY` — required for `/intake/places/*` and `/discovery/run`
- `GOOGLE_PLACES_DEFAULT_RADIUS_M` — defaults to 32186 (~20mi)
- `MAILPIT_ENABLED` — defaults to true; set false to disable the email bus entirely
- `MAILPIT_SMTP_HOST` — defaults to `localhost`
- `MAILPIT_SMTP_PORT` — defaults to `1025`
- `MAILPIT_API_BASE` — defaults to `http://localhost:8025`
- `TAVI_EMAIL_DOMAIN` — defaults to `tavi.local` (used for `tavi+{wo_id}@{domain}` plus-addressing)
