# vendor-discovery-build

_Session ID: 1ce783c7-40b3-4c02-812c-cd03162e989d_

_Started: 2026-04-19T18:31:57.981Z_

_51 messages (prose only; tool calls, tool results, and CLI command noise omitted)_

---

## User

makre sure u are familiar with the project and the claude.md file. this chat will be used for the 2nd part which is vendor discovery. to start off, i need you to generate 2 types of fake data. 1st type should be a standard message asking for a service (like the example given in the instructions pdf). there is also other metadata associated w the chat, such as location etc. 2nd type of data should be related to the 1st. given each service request (1st type of fictitious data) there should be another block of data similar to what you would get from the Google Places API that provides nearby locations that can complete the given service, and their details. got it?

---

## Claude

I'll explore the project first to understand what's there, then we can discuss the data generation approach before I write anything.

---

## Claude

Got it. Quick read-back before I generate so we don't waste cycles:

**Type 1** — what a customer-side submission looks like end-to-end:
- Original chat message ("I need this plumbing job done for my Dallas location at 123 Main St for $250 by Tuesday 5pm…")
- Structured metadata mirroring the intake service's `WorkOrder` schema: `trade`, `address_line/city/state/zip/lat/lng`, `description`, `urgency`, `scheduled_for`, `budget_cap_cents`, `requires_licensed/insured`, etc. So vendor discovery can consume it as if intake handed it off.

**Type 2** — per-request nearby-vendor block, shaped like a real Google Places "Nearby Search" + "Place Details" response (`place_id`, `name`, `geometry.location`, `vicinity`, `rating`, `user_ratings_total`, `types`, `business_status`, `opening_hours`), **plus** the other signals the PDF calls out (BBB rating, license #/status, years_active, insured) — since the scoring layer needs more than a single Google rating to differentiate. I'd deliberately spread the signals so scoring is non-trivial (5★/3-reviews vs 4.2★/800-reviews vs unlicensed cheap option, etc.).

Before I start, two things worth pinning:

1. **Where it lives** — vendor discovery is a separate project per CLAUDE.md. Want me to create `vendor-discovery/` here as a sibling to `backend/`, or generate the seed files at repo root and you'll wire them in later? My default: new `vendor-discovery/` dir with `data/seed/` for fixtures (JSON), since you'll need a project there anyway.

2. **Volume + spread** — defaulting to ~10 requests covering all 6 trades, varied cities (Dallas, Austin, Houston, Phoenix, etc.), 12–20 vendors per request. Push back if you want fewer/more, or want everything concentrated in one metro for a tighter demo.

Confirm or redirect, then I'll generate.

---

## User

1. just to clarify, its all 1 priject im just approaching it in 3 different subparts thru 3 different claude code sections. everything lives together in the current folder.update claude.md and otehr relevant tfiles to repflect this 2. yes good.

---

## Claude

Loading task tools to track this since it's multi-step.

---

## Claude

Done. Summary:

**Updated** — `CLAUDE.md` reframed as one unified project with three subparts (`backend/` intake, `vendor-discovery/`, future vendor-contact). Preserved the architecture / Google Places content you added; moved intake-specific details under the "Subpart 1" section.

**Created** — `vendor-discovery/data/seed/`:
- `requests.json` (15K) — 10 work orders covering all 6 trades × all 4 urgencies × 10 cities × 4 chat channels (voice, web_chat, sms, mobile_app). Each entry pairs the raw FM chat message with the structured `work_order` intake would have extracted.
- `generate_places.py` (26K) — deterministic generator (seed `20260419`). 8 archetypes: premium / solid-mid / suspicious-five-star / mediocre / low-quality / out-of-radius / expired-license / residential-only. Trade-aware names + license classes; state-aware area codes + license prefixes.
- `places.json` (377K) — 166 vendors, 14–20 per request. Full Google Places shape (`place_id`, `geometry`, `rating`, `user_ratings_total`, `types`, `business_status`, `opening_hours`, `price_level`, `url`, etc.) + `extended_signals` block (BBB, license, insurance, years-in-business, service radius, commercial acceptance, min service charge, distance).

**Signal spread** — ratings 2.8–5.0, reviews 3–1,113; 25% don't accept commercial, 25% uninsured, 9% out-of-20mi, 4% expired license; BBB from A+ to D plus NR. Scoring will have real work to do.

Each vendor carries a debug-only `_archetype` tag — scoring code must ignore it. Regenerate any time by re-running the script.

---

## User

good. for now though revise so that the only chat chanell is text. were doing text-only comms first and then adding voice. and by a text-only chat channell i mean that the client is requesting a service by writing into a chatbox from the frontend.

---

## Claude

Switching all channels to text-only chat.

---

## Claude

Done. All 10 requests now use `"channel": "web_chat"` — a customer typing into the frontend chatbox. Nothing else changed; `places.json` and CLAUDE.md are already aligned (voice is listed as deferred in the stack, and Subpart 1 already describes chat as text-only).

---

## User

make a new subsection in requests, that contains the requests in separate messages. that way you generate all the info for a specific order, but it gets sent in phases by parts to be more realistic. this will allow for more robust testing of the LLM to see if it can ask the right followup questions. understood?

---

## Claude

Understood. Let me confirm my design choices before executing since this restructures the data shape:

**Approach**: New `turns` array nested inside each `chat` object, alongside the existing `message`. Each turn is `{content, sent_at}` — customer messages only (no bot turns, since the point is to test whether the LLM asks the right follow-ups). Casual chat-style writing (lowercase, terse, one detail per message). 3-5 turns per request based on complexity. Timestamps spread across a realistic 2-3 min conversation ending at `submitted_at`.

**Kept**: The monolithic `message` stays as a single-shot ground-truth reference — useful for diffing extraction results against the "all info in one shot" case. Nothing else changes.

Executing now.

---

## Claude

Done. `requests.json` schema bumped to v2; each `chat` object now has a `turns` array alongside the original monolithic `message`. 

**Shape per turn**: `{content, sent_at}` — customer-only, no bot turns (the intake LLM's follow-up behavior is the thing under test).

**Distribution**: 40 turns total, 3–5 per request based on complexity:
- 3-turn: req 3 (Phoenix HVAC emergency), req 9 (Denver daycare) — urgent, terse
- 4-turn: reqs 1, 2, 5, 7, 8, 10 — typical
- 5-turn: req 4 (Atlanta HVAC quarterly), req 6 (Austin electrical install) — most detail

**Style**: casual chatbox tone (lowercase, short, one or two details per message). Timestamps monotonic within each conversation, spanning ~2–3 min ending at `submitted_at`. `work_order.created_at` falls 0–2 min after the last turn, representing the LLM-extraction + user-confirmation window.

`_meta.schema_notes` documents the difference between `chat.message` (single-shot ground truth) and `chat.turns` (phased for follow-up testing). `places.json` unaffected.

---

## User

im ready to move on with the vendor discovery phase of the project. i just got access to Google Places API (new). make a plan for how you would use this API to access service locations, reviews, contact info and other data. ensure that when using the API, you only call for data you need, (eg. no images) as not to overcharge and run out of API free limit for no reason. your plan should also include ideas for giving each location a rating (factoring in reviews + review count) etc. from Google Places but also from other sources. Lastly, you should be able to get scheduling time from each place, and if a client wants the service done at a time when a particular store would be closed, it would be automatically discarded from the rankings (ie. they are adaptive for each request). ready? go.

---

## Claude

I'll follow the plan workflow. Starting with Phase 1 — understanding the current codebase state and confirming scope.

---

## Claude

Good map. The backend is much further along than I expected — there's full intake scaffolding (FastAPI + routers/intake + agent.py + Next.js frontend). That means the vendor-discovery work plugs into an existing, working service rather than scaffolding from scratch.

Writing an initial plan draft, then launching a Plan agent to verify the Google Places API (new) specifics and stress-test the scoring design.

---

## Claude

Launching a Plan agent to verify Google Places API (new) specifics and stress-test my proposed scoring design.

---

## Claude

Plan agent flagged a few meaningful corrections — especially on Google Places API (new) field mask tiering (my initial SKU map was off) and hours-edge-cases. Now drafting the final plan.

---

## User

[Request interrupted by user for tool use]

---

## User

no fake enrichment. really, lets only use 2 sources for now, the Google Places API, and the BBB for rankings. I want all data to be real. we should also have at least 2 new tables in our database. firstly yes, we need a vendors table, where we store each vendor id along with all of its stats. in this table, using all objective stats (reviews, review_count, BBB rating, tenure) we should create a cumulative rating (or score) for this business. then, later we add another table for 'negotiations' this should have rows with an orderId, a vendorID (that we can then use to access the vendor table), and more details such as vendor ranking for this particular order (subjective) (eg. user might only care abt cheapest price rater than licensure, or might be in an emergency and want fast or 24/7 service). this 2nd table should also have spaces to store statuses of communication with the vendor (ie. reached out, quoted, completed), as well as space for including messaging history w the vendor, but those will be filled in later. and yeah, the DiscoveryRun table was a good idea. do you get these changes? basically we're trying to simplify a lot. only 2 sources for now Google places + BBB. revise the plan and add those tables to the database. ask questions if you have

---

## Claude

Got the shift. This simplifies a lot — no synthetic anything, two real sources, and the score splits cleanly into objective (on `vendors`) vs subjective-per-order (on `negotiations`).

Before I revise the plan, one real blocker and a couple of choices I'd rather confirm than guess:

---

## Claude

Clear. Revising the plan: no synthetic data, two sources (Google Places + scraped BBB), license/insurance handed off to subpart 3, score splits into objective (Vendor.cumulative_score) and subjective (Negotiation.subjective_rank_score). Writing the revision.

---

## Claude

Got it, the email is already stripped from the plan (the only change was that UA string — now reads `User-Agent: TaviBot/0.1 (hackathon research)`).

To move from plan mode into implementation, I need `ExitPlanMode` approved — that's the mechanism the harness uses to switch states, not a second-pass review. Clicking **Approve** on this next prompt is how you greenlight me to start writing code and running the backend.

---

## User

yes, implement

---

## Claude

Out of plan mode. Cleaning up stale tasks and creating new ones for the discovery implementation.

---

## Claude

Subpart 2 is wired end-to-end. Summary:

**Built**
- `backend/app/models.py` — `Vendor` (26 cols, place_id PK), `Negotiation` (14 cols, includes `messages` + `actions_log` placeholders for subpart 3), `DiscoveryRun` (audit + cost counters)
- `backend/app/services/discovery/` — 9 modules: `places_client.py` (Google Places New + field masks), `bbb_client.py` (polite scraper, rate-limited 1 req/s, no personal info in UA), `geocoding.py`, `hours.py` (cross-midnight + 24/7 + permissive-on-missing), `scoring.py` (Bayesian rating + cumulative composite + per-urgency subjective weights), `filters.py` (hard gates), `cache.py`, `trade_map.py`, `orchestrator.py`
- `backend/app/routers/discovery.py` — `POST /discovery/run`, `GET /discovery/run/{id}` registered in `main.py`
- `backend/app/enums.py` — `EngagementStatus` enum (prospecting → contacted → … → completed; declined/ghosted off-ramps)
- DB initialized — `vendors`, `discovery_runs`, `negotiations` tables now alongside `work_orders`
- 28 unit tests passing (hours edge cases, scoring math, BBB parser against HTML fixtures)

**To run live discovery**
1. Add your key to `backend/.env`: `GOOGLE_PLACES_API_KEY=...`
2. Submit a work order via the existing intake UI (or use `wo_smoke_dallas` already inserted)
3. `curl -X POST http://localhost:8000/discovery/run -H 'Content-Type: application/json' -d '{"work_order_id":"<id>","refresh":true}'`

The endpoint returns `{run, ranked, filtered}` — `ranked` is the survivors with `subjective_rank_score` + breakdown, `filtered` shows who got dropped and why (audit trail). Cost-wise: searchNearby is free (IDs-only mask), each new vendor costs 1 Enterprise details call (1k/mo free), and BBB is free but rate-limited at 1 req/sec.

---

## User

here's my key, add it in: [REDACTED_GOOGLE_PLACES_KEY]. now, before we test, it, add back the column to the work_orders table, so that it has exact address (using Google Places API autocomplete) and is able to calculate distances. afterwards, i will submit a request thru the frontent UI, and I want you to tell me how to verify correct output.

---

## Claude

On it. Three pieces: stash the key (without echoing it), restore address fields as required with a backend autocomplete proxy, wire the frontend to use it. Let me check a few files before touching anything.

---

## Claude

Everything wired. Summary + verification steps:

**Built**
- Key → `backend/.env` (gitignored, not echoed anywhere)
- `models.py`: address_line/city/state/zip/lat/lng now NOT NULL on `WorkOrder`
- `schemas.py`: `REQUIRED_FIELDS` expanded; `WorkOrderRead` tightened; new Pydantic schemas for Places proxy
- `places_client.py`: `autocomplete()`, `get_address_details()` (cheap Essentials mask), `parse_address_components()`
- `routers/places.py`: `POST /intake/places/autocomplete`, `POST /intake/places/select` — registered in `main.py`
- `prompts.py`: one-line nudge so the intake LLM tells the user to pick an address if missing
- `frontend/app/page.tsx`: debounced address autocomplete with dropdown; picked suggestion populates fields
- DB dropped + recreated; all 28 tests still pass; live autocomplete + select confirmed against real Google API

**To test (two terminals)**

Terminal 1 — backend:
```
cd backend && uv run uvicorn app.main:app --reload --port 8000
```
Terminal 2 — frontend:
```
cd frontend && npm run dev
```
Then open `http://localhost:3000`.

**What a correct run looks like**

1. **Address picker**: type a real commercial address (e.g. "`4320 Westheimer Houston`"). You should see a dropdown of Google suggestions within ~300ms. Click one → the box turns green with the formatted address + lat/lng, and the right-sidebar shows Street / City / State / ZIP filled in.

2. **Chat**: describe the job (trade, timing, budget, urgency, etc.). The LLM will not ask for your address — it's been told the UI handles that. The "Ready" pill + **Submit** button only appear once the chat fields AND the address are both set.

3. **Submit**: confirms "Submitted. Work order ID: `<uuid>`" in the chat panel. Copy that ID.

4. **Discovery**: in a third terminal, run:
   ```
   curl -s -X POST http://localhost:8000/discovery/run \
     -H 'Content-Type: application/json' \
     -d '{"work_order_id":"<id from step 3>","refresh":true}' | python3 -m json.tool | head -80
   ```
   Expect:
   - `run.strategy` = `searchNearby` (for plumbing/electrical/hvac/handyman) or `searchText` (lawncare/appliance_repair)
   - `run.candidate_count` = up to 20
   - `run.api_detail_calls` > 0 on first run, `= 0` on second (cache hit) if you re-run without `refresh: true`
   - `run.bbb_scrape_count` = number of new vendors scraped (first run only)
   - `ranked` is a list with `rank: 1, 2, 3, …` — each entry has `negotiation.subjective_rank_score` and `vendor` with real `display_name`, `google_rating`, real `formatted_address`, and (for vendors that have a BBB profile) `bbb_grade` + `bbb_complaints_total`
   - `filtered` shows vendors dropped with `filter_reasons` like `["distance_exceeded_24.3mi"]` or `["hours_closed_at_local_Sun_21:30"]`

5. **Inspect the DB** (`sqlite3 backend/tavi.db` then `.schema` / `SELECT * FROM vendors;` etc.) — you should see a populated `vendors` row per candidate with real Google fields + BBB fields where found, one `discovery_runs` row, and one `negotiations` row per (work_order × vendor).

**Watch for**

- BBB enrichment is best-effort — some vendors (especially small shops) won't have a BBB profile. Those rows will have `bbb_grade: null` and their `cumulative_score` reweights onto the rating alone. That's correct behavior, not a bug.
- First discovery run on a new address will take ~5–15 seconds (Google details calls + BBB scrape at 1 req/sec). Subsequent runs on the same address hit the cache and return instantly.
- If the urgency is `scheduled` or `flexible`, 24/7 vendors won't get a 24-hour bonus — the weight profile explicitly zeroes that signal for non-urgent jobs.

---

## User

build a neat UI that allows me to see clearly the SQLite databases that were populated during testing

---

## Claude

On it. Four tabs — one per table — with row expansion for JSON/long fields. Read-only admin endpoints on backend, Tailwind table UI on frontend.

---

## Claude

All endpoints return real data from your test runs — the DB currently has 2 work orders, 20 vendors, 1 discovery run, 20 negotiations. My uvicorn attempt hit port 8000 in use (your backend is already running with `--reload`), which is why `/admin/*` responded fine — your server auto-reloaded on my edits.

---

## Claude

**Done.** Open http://localhost:3000/admin (or click **DB Explorer →** in the top-right of the intake page).

**What you'll see**
- **Sticky header** with 4 tabs, each showing a live row count badge. Refresh button top-right.
- **Work Orders** tab: ID, trade + urgency badges, city, scheduled time, budget, licensed/insured, created. Click a row to expand — shows full description, address + lat/lng, access notes.
- **Vendors** tab: display name (Google place_id preview underneath), city, rating with review count, color-coded BBB grade, years in business, cumulative-score bar (green ≥ 0.75, yellow ≥ 0.5, red below), 24/7 flag, operational status. Expand for full address, phone, clickable BBB profile + website links, and the full score-breakdown JSON + hours JSON.
- **Discovery Runs** tab: run ID, work-order ID, strategy + urgency profile badges, candidate/cache-hit/API-calls/BBB-scrapes counters color-coded for quick scanning, duration in seconds. Expand for cost note.
- **Negotiations** tab: rank badge (or red "filtered" badge), vendor name, work-order ID, engagement status badge, subjective score bar, filter-reason chips (red). Expand for the full subjective-rank breakdown JSON + empty messages/actions_log placeholders (those fill in during subpart 3).

**Visual touches**
- Trade/urgency/BBB/status all have consistent color families so you can scan at a glance (plumbing=sky, hvac=amber, electrical=yellow, A+=emerald, D/F=red, etc.).
- Score bars are inline and give you a sense of distribution without reading numbers.
- JSON blobs render in monospaced boxes with horizontal scroll, capped at 16rem height.
- Sort is "most useful first" per tab — newest work orders, highest-scoring vendors, newest runs, latest negotiations top.

No auth, no edits — strictly read-only. Click any row to expand; click again to collapse.

---

## User

great. lets make a few more changes. firstly, let's reset the default user's preferences to blank.i dont want to work with differemt user types at the moment. also, make it so that whenever a chat order request goes through, phase 2 runs automatically right after, and populates tables so that they can immediately (or a few seconds of delay) be seen in the database and in the table ui.

---

## Claude

Two changes. Let me tackle them in order.

---

## Claude

Done. Three changes, all applied:

**1. Blank user profile** — `prompts.py` `USER_PROFILE = {}`, prompt updated to "treat every conversation as a first-time user; ask for license/insurance/quality directly." `agent.py` now skips injecting the profile message entirely when empty. No more Lucas / Acme FM / default_quality_threshold=4.0 baked in.

**2. Auto-discovery** — `/intake/confirm` now schedules a FastAPI `BackgroundTask` that runs `run_discovery()` on a fresh DB session right after the work order is persisted. Response returns immediately; discovery populates `vendors`, `discovery_runs`, `negotiations` 5–15 seconds later. Failures are caught + logged — they never break intake.

**3. Frontend submitted-state** — now reads: _"Submitted. Work order `<id>` created. Vendor discovery is running in the background — results will appear in the DB Explorer in a few seconds."_ with a **View in DB Explorer →** link.

**To try it**
1. File a new work order via the intake chat (the LLM will now ask about licensed/insured since no defaults are seeded).
2. Hit Submit.
3. Click **View in DB Explorer →** — the Work Orders tab will show your new row immediately. Discovery Runs / Vendors / Negotiations tabs will be empty for a few seconds, then populate (hit **Refresh** to see them appear).

Your backend is running with `--reload` so the changes are already live. Tests still green (28 passed).

---

## User

i noticed the threshold for stars cutoff isnt being asked in the chat session and appears to be arbitrary when i look at the table ui. I also noticed that in one of the chats, the agent knew the adress (extracted it from the chat) before i entered it using the Google Places Autocomplete. so 2 fixes: make sure you're asking for a cuttof, or at least assuming one but letting the user adjust it if desired. and the other fix is maybe if the adress is clearly provided in the chat, fill in the adress in the api box and and maybe let the user confirm / change it if its incorrect. lsatly a 3rd error i noticed the discovery runs appear to have stopped being called (all of phase 2 doesnt look active anymore after the chat goes through). diagnose and fix this issue as well.

---

## Claude

Let me diagnose the discovery issue first — need to see current DB state.

---

## Claude

Found the bug. Let me show you what happened:

---

## Claude

Diagnosis: Places API (new) doesn't accept `general_contractor` — it's 400 INVALID_ARGUMENT. HVAC + handyman were using it and silently failing in the background task (caught + logged, never visible to you). Tampa handyman was your two most recent orphans, confirms it.

Making all three fixes now.

---

## Claude

Handyman discovery works now — 19 candidates, 30s. Moving to issues #1 and #2.

---

## Claude

All three fixes landed. Your backend is on `--reload` so they're already live. Summary:

**#1 — Quality threshold now asked**
- Added `quality_threshold` to `REQUIRED_FIELDS`; system-level `is_ready` blocks submit until it's set.
- Tool description reworded: *"REQUIRED — always confirm a value. If they waffle, set 4.0 and mention it. Never silently skip."*
- Prompt line added: *"Any minimum vendor rating, or should I default to 4.0?"* — so the LLM explicitly surfaces the question.
- The sidebar already shows "Min vendor rating" — so once the LLM sets it, you'll see the exact value, not an arbitrary 4.0 carried over from the old profile.

**#2 — Chat-extracted address seeds autocomplete**
- New `address_hint` field on `WorkOrderPartial` + on the `update_fields` tool schema (free-text only, NOT persisted).
- Prompt: *"If the user mentions an address in passing, call update_fields with address_hint set. The UI pre-fills its autocomplete input so the user can confirm/pick the precise match. Don't re-send unless the user says something different."*
- Frontend watches `fields.address_hint` and seeds `addressQuery` exactly once (and only when the user hasn't typed or selected anything). The existing 300ms-debounced autocomplete then fires on its own → dropdown appears → user picks → `/select` geocodes and stamps structured address.
- `address_hint` hidden from the captured-fields sidebar so it doesn't look like a durable field.

**#3 — Discovery auto-run fixed**
- Root cause: `trade_map.py` mapped HVAC + handyman to `includedTypes=["general_contractor"]`. The new Places API rejects that with `400 INVALID_ARGUMENT: Unsupported types: general_contractor`. The background task caught the error and logged it, so nothing surfaced — you just saw no discovery runs.
- Fix: HVAC + handyman now use `searchText` (same pattern as lawncare + appliance_repair). Kept the `name_keywords` post-filter for extra precision.
- Verified: re-ran discovery against your orphan Tampa handyman work order → 19 candidates, 19 API calls, 16 BBB scrapes, 30s duration. **Plumbing / electrical trades were never affected** (they use valid types `plumber` / `electrician`).

Plumbing + electrical stayed healthy throughout; the 4 orphans in your DB are the two Tampa handymans that hit the bug plus two earlier work orders submitted before the auto-trigger existed. Re-submitting any of them (or submitting new ones) will now populate `vendors` / `discovery_runs` / `negotiations` ~15–30s after confirm.

All tests green (28). Frontend typecheck clean.

---

## Claude

No response requested.

---

## User

give a summary of what we have in this project so far

---

## Claude

Let me pull the current state from the repo rather than summarize from memory.

---

## Claude

## Project state

**Tavi** — AI-native managed marketplace for blue-collar trades. One repo, one FastAPI backend, one Next.js frontend, three subparts of the product flow.

### Subpart 1 — Intake (done)

Chat → structured work order → persisted + phase 2 auto-trigger.

- **Backend**: `backend/app/routers/intake.py` (`/intake/start`, `/intake/chat`, `/intake/confirm`) + `backend/app/routers/places.py` (`/intake/places/autocomplete`, `/intake/places/select`).
- **LLM**: Anthropic tool-use loop in `agent.py` (`update_fields` tool). Profile is **blank** — no per-user defaults seeded. Prompt in `prompts.py` asks for licensed / insured / quality_threshold directly each conversation; if user waffles on quality_threshold, LLM defaults 4.0 and acknowledges.
- **Address flow**: Google Places (new) autocomplete widget on the frontend. LLM doesn't set address fields via the tool — but it does extract addresses mentioned in chat into `address_hint`, which the frontend uses to pre-fill the autocomplete input so the user can confirm.
- **Frontend**: `frontend/app/page.tsx` — debounced autocomplete dropdown, captured-fields sidebar, submit gated by 14 REQUIRED_FIELDS (including `quality_threshold` + full address + lat/lng).
- **Persist**: `work_orders` table via `services/intake.py`. `/intake/confirm` returns immediately and schedules phase 2 as a FastAPI BackgroundTask.

### Subpart 2 — Vendor discovery (done)

Work order → live Google Places + scraped BBB → scored + ranked vendors.

- **Services** under `backend/app/services/discovery/`:
  - `places_client.py` — searchNearby (IDs-only, free), searchText (IDs-only, free), getPlace (Enterprise mask, 1k/mo free) — explicitly no photos/reviews
  - `bbb_client.py` — httpx + BeautifulSoup scrape of bbb.org, rate-limited 1/sec, generic UA
  - `trade_map.py` — plumbing/electrical use `searchNearby` with valid types; hvac/handyman/lawncare/appliance_repair use `searchText` (general_contractor bug fixed)
  - `hours.py` — cross-midnight + 24/7 + missing-hours overlap checks using utcOffsetMinutes
  - `scoring.py` — Bayesian-adjusted rating + BBB grade + resolution rate + tenure → `cumulative_score` (objective, on Vendor); blended with distance/24-7/budget-fit into `subjective_rank_score` (per-order, on Negotiation) via four urgency weight profiles
  - `filters.py` — hard gates: business_status, >20mi, hours, quality_threshold vs Bayesian rating
  - `orchestrator.py` — runs it all, persists DiscoveryRun + Vendor + Negotiation rows; 24h idempotency window unless `refresh=true`
- **Router**: `backend/app/routers/discovery.py` (`POST /discovery/run`, `GET /discovery/run/{id}`).
- **DB tables**: `vendors` (26 cols — cached Places + BBB + cumulative_score), `discovery_runs` (audit + cost counters), `negotiations` (per work_order × vendor, with placeholder `messages`/`actions_log` JSON for subpart 3). Live counts: **72 vendors, 5 discovery runs, 82 negotiations** from your testing.
- **License/insurance**: deliberately not enforced here — deferred to subpart 3's outreach.

### Subpart 3 — Vendor contact / auctioning (not started)

Only the DB plumbing exists:
- `Negotiation.status` enum (`EngagementStatus`: prospecting → contacted → quoted → negotiating → dispatched → completed; declined/ghosted off-ramps), default `prospecting`
- `Negotiation.messages` + `Negotiation.actions_log` empty JSON columns ready to fill

No outreach, no state machine driver, no kanban UI yet. Per the hackathon rubric this is where most engineering time should land.

### Admin DB Explorer (done)

- **Backend**: `backend/app/routers/admin.py` — `GET /admin/overview,work_orders,vendors,discovery_runs,negotiations`. Negotiations joined with vendor display_name for readability.
- **Frontend**: `frontend/app/admin/page.tsx` — four-tab sticky-header UI with row counts, color-coded badges (trade / urgency / BBB grade / engagement status), inline score bars, row-click expansion showing JSON breakdowns + full addresses + BBB links. Linked from intake header.

### Tests

28 passing under `backend/tests/` — hours edge cases (cross-midnight, 24/7, missing), scoring math (Bayesian anchor, weight profiles per urgency, reweight on missing BBB), BBB HTML parser against inline fixtures.

### Infra

- **Stack**: FastAPI + SQLAlchemy (SQLite at `backend/tavi.db`) + Anthropic Claude (Haiku 4.5) + Google Places (new) + httpx/BeautifulSoup. Python managed via `uv`.
- **Frontend**: Next.js 16 / React 19 / Tailwind v4.
- **Env**: `GOOGLE_PLACES_API_KEY` + `ANTHROPIC_API_KEY` in `backend/.env` (gitignored). `vendor-discovery/data/seed/` holds the original fixture generator from before API access — unused at runtime now, kept for archival.

### What's missing / next

1. **Subpart 3** — the actual ranked, agentic outreach: email + SMS drafters, human-in-the-loop approval flow, vendor-response ingestion, state-machine transitions, LLM-simulated vendor persona for demo, kanban command center UI.
2. **Discovery UX on frontend** — right now you inspect results via `/admin`; the intake page doesn't show the ranked list after submit. A post-submit "Vendors found" view + a manual "refresh discovery" button would tighten the demo.
3. **DST-boundary edge case** in hours (utcOffsetMinutes is a fetch-time snapshot) — documented, not fixed.
4. **Licensed / insured verification** — deferred to subpart 3 as planned; currently just flags carried forward on the Negotiation row.

---

## User

update claude.md to reflect this, if needed.

---

## Claude

Read-through: CLAUDE.md is already accurate — commit `62b4c8d` refreshed it and all the subsequent changes (blank user profile, `address_hint`, `quality_threshold` in REQUIRED_FIELDS with 4.0 fallback, `searchText` for hvac/handyman after the `general_contractor` bug, auto-discovery on confirm) are already reflected. 

The only real gap: the `Unit tests: uv run pytest` line doesn't say what's covered.

---

## Claude

Single one-line tweak — expanded the `pytest` command description to name what's covered. Everything else in CLAUDE.md is already current: it correctly describes the blank user profile, `address_hint` flow, required `quality_threshold` with 4.0 fallback, `searchText` routing for the four trades the new Places API rejects for type-based nearby search, auto-discovery background task on `/intake/confirm`, the `vendor-discovery/` directory as archival, and the admin DB explorer.

---

## User

we'll want to change the ranking function a little bit. when we do part 3, well be getting quotes from the vendors, as such, we want to incorpotate their quotes into the rankings. therefore even though each vendor should stiull have an objective rating, we want to subjective ranking to only be generate once teh vendor gets back with a quote for us, and we want to incorporate this quote into the ranking. then, furthermore well want to assume weights for factoring price vs quality based on status (emergency vs free to schedule anytime). so eventually we'll come back to the facility manager after doing part 3, and give him out ranking of vendors, being clear what we're optimixing for but giving him tbhe choice to change if he wants to optimize for price / quality. therefore the ranking function should be able to change based on fm's response. so redo the ranking function, and it shouknd't be available until we gat part 3 working.

---

## Claude

Makes sense — the objective score stays stable per vendor; the order-specific rank only gets meaning once we have quotes to compare. Let me plan the changes before I start:

- **Models**: add a `quote_cents` nullable column on `Negotiation` (empty until subpart 3 ingests a quote)
- **Scoring**: replace `compute_subjective`/`SUBJECTIVE_WEIGHTS` with a quote-aware version — `RankingWeights` (quality vs price) + `default_weights_for(urgency)` + `price_fit(quote, budget)` + a compute fn that takes `quote_cents` and a weights arg. Design it so a FM override just passes different weights.
- **Orchestrator**: stop computing subjective scores at discovery time. Just create `Negotiation` rows for survivors + filtered, all with `subjective_rank_score=None`, `rank=None`. `cumulative_score` on Vendor still computed.
- **DB**: `ALTER TABLE negotiations ADD COLUMN quote_cents` + `UPDATE` existing survivor rows to null out stale subjective data. Keeps your 8 work orders / 72 vendors / 5 runs / 82 negotiations intact.
- **Admin UI**: Negotiations tab — replace "Score" with a "Quality" column (joined `vendor.cumulative_score`), show `pending` / `filtered` in the Rank column, add a placeholder "Quote" column for subpart 3.
- **Tests**: rewrite the 4 subjective tests (old distance/24-7/urgency-distance logic is gone).
- **CLAUDE.md**: update subpart-2 scoring section + subpart-3 plan to describe the new model.

---

## Claude

All 7 tasks done. 38 tests passing.

**What changed**

- **Models / DB**: new `Negotiation.quote_cents INTEGER` column (ALTER TABLE, no data loss). Existing survivor rows had stale `subjective_rank_score` / `rank` / `subjective_rank_breakdown` cleared so the admin UI doesn't show misleading values. Your 8 work orders / 72 vendors / 5 runs / 82 negotiations are intact.
- **`scoring.py`**: 
  - `RankingWeights(quality, price)` frozen dataclass, validates sum ≈ 1.0
  - `DEFAULT_WEIGHTS_BY_URGENCY`: emergency 0.80/0.20 → flexible 0.35/0.65
  - `PRESET_WEIGHTS`: `balanced`, `quality_leaning`, `price_leaning`, `quality_only`, `price_only` — ready for the "re-rank by X" buttons
  - `price_fit(quote, budget)`: 1.0 at ≤50% of budget, linear decay to 0.0 at 2× budget
  - `compute_subjective(cumulative_score, quote_cents, budget_cap_cents, weights)` — returns a score + full breakdown (weights, raw signals, per-axis contributions). Will be invoked by subpart 3 once quotes arrive.
- **Orchestrator**: discovery no longer computes subjective rank. Survivors land with `filtered=False`, `rank=None`, `subjective_rank_score=None`, `quote_cents=None` at status `prospecting`. Cumulative score on Vendor still computed as before.
- **Router sort**: `_hydrate` + admin negotiations now sort by `(filtered asc, rank asc nulls-last, vendor.cumulative_score desc)` — so once subpart 3 fills `rank`, it takes over; until then quality-first is the proxy ordering.
- **Admin UI — Negotiations tab**: new blue info banner explaining the pre-quote state. Columns are now **State / Vendor / Work Order / Status / Quality / Quote / Rank / Filter reasons / Created**. Quality is the joined `vendor.cumulative_score` bar; Quote shows `—` with a tooltip "Awaiting vendor response (subpart 3)"; Rank shows `—` with "Ranked after quote arrives" tooltip. The expand row's subjective breakdown says "awaiting quote" when null.
- **Tests**: dropped the 4 obsolete subjective tests (distance/24-7/urgency-distance). Added 10 new ones: `RankingWeights` sum validation, defaults per urgency, preset weights, emergency-favors-quality, flexible-favors-price, `price_fit` edge cases (0.4x / 0.5x / 1.0x / 2.0x / zero-budget), `compute_subjective` rewards cheaper quote, weight profiles swing outcome between quality-leaning and price-leaning views on the same pair of vendors.
- **CLAUDE.md**: subpart-2 scoring section now reflects the deferred subjective rank. Subpart-3 section documents the new helpers (`RankingWeights`, `default_weights_for`, `PRESET_WEIGHTS`) and the planned re-rank UX flow.

The ranking function is now a pure, pass-in-weights function ready for subpart 3 to drive — but the orchestrator doesn't call it, so rank columns stay deliberately empty until the outreach agent has quotes to feed in.

---
