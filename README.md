# Tavi

AI-native managed marketplace for blue-collar trades. Facility managers submit a work order in chat; the system discovers nearby vendors, runs a multi-modal agentic auction, verifies credentials, and books the winner — all autonomously.

Hackathon build. Three subparts in one repo: chat intake, live vendor discovery, agentic vendor auction. See [`CLAUDE.md`](./CLAUDE.md) for full architecture notes.

## Quick start

**Prereqs**

- **Node 20+** (with `npm`) — install from [nodejs.org](https://nodejs.org) or `brew install node`. This is the only thing `make setup` can't auto-install for you; everything else below is handled by its preflight.
- Python 3.11+ — `uv` will bootstrap this if missing.
- `uv` — `make setup` will auto-install via the official script if it's not on your PATH.
- npm version — `make setup` auto-upgrades via `npm install -g npm@latest` if your npm is older than v10.
- API keys: Anthropic + Google Places (New). See [Getting API keys](#getting-api-keys) below.

**Run it**

```bash
git clone <this-repo> && cd TaviHackathon
make setup                # installs deps, creates backend/.env, inits SQLite
# edit backend/.env with your Anthropic + Google Places keys
make dev                  # runs backend on :8000 and frontend on :3000
```

Open http://localhost:3000.

## First-run checklist (API setup)

Both providers have non-obvious gotchas that cause silent failures the first time. Work through these in order; `make doctor` at the end validates the whole chain against the live APIs before you waste time launching the UI.

**Anthropic** ([console.anthropic.com](https://console.anthropic.com))

1. **Create an API key** — Settings → API Keys → Create Key. Must have the `sk-ant-api03-...` prefix.
2. **Add prepaid credits** — Billing → Buy Credits. A full demo costs well under $1, but a $0 balance will fail every call immediately.
3. **Confirm model access** — the default model is `claude-sonnet-4-6`. If your account doesn't have Sonnet 4.6 access, either request it in Settings → Models, or set `ANTHROPIC_MODEL=claude-sonnet-4-5` in `backend/.env`.

**Google Places (New)** ([console.cloud.google.com](https://console.cloud.google.com))

1. **Create or select a project** — Project picker in the top bar.
2. **Enable billing on the project** — Billing → link a billing account with a credit card. *This is the #1 silent failure:* without billing, every call returns 403 with a billing-masquerading-as-permission error. Free-tier credits still require a card on file.
3. **Enable the right API** — APIs & Services → Library → search literally for **"Places API (New)"** → Enable. The legacy "Places API" is a different SKU and will 403 against the endpoints this project uses.
4. **Create an API key** — APIs & Services → Credentials → Create Credentials → API key.
5. **Leave the key unrestricted for local dev** — if you add HTTP referrer or IP restrictions, server-side calls from your laptop will be blocked. For production, restrict to your server IPs; for this hackathon demo, leave it open.

**Validate**

```bash
make doctor
```

This makes one cheap call to each provider (~1 Anthropic token, 1 Google Places searchText). It distinguishes between: missing key, bad key, billing not enabled, wrong API enabled, model-access denied, and key-restriction blocks — with per-case fix instructions. Run it anytime your `.env` changes.

Typical cost for a full demo run (one work order, 12 vendors, ~10 scheduler ticks): under $1 in Anthropic + a few cents in Google Places.

## What it does

1. **Intake** — chat with the facility-manager-facing agent at `/`. It extracts trade, service address (via Google Places autocomplete), scheduled date, budget, urgency, quality threshold, and license/insurance requirements. On confirm, the work order is persisted and discovery kicks off in the background.
2. **Vendor discovery** — live Google Places search (~20mi radius, trade-aware strategy) + BBB scrape for grade/accreditation/complaints/tenure. Each vendor gets a cumulative objective score; hard filters remove out-of-hours and sub-threshold vendors.
3. **Vendor auction** — per-work-order command center at `/work-orders/{id}`. Click **Tick** to advance the scheduler: Tavi pitches prospects, vendor-simulator agents reply (some ghost, some refuse, most engage), quotes land, the leaderboard re-ranks live, credentials get verified, the top-ranked vendor is booked and the rest auto-decline.

Read-only DB explorer at `/admin` for debugging the raw state end-to-end.

## Stack

- **Backend** — FastAPI + SQLAlchemy (SQLite file at `backend/tavi.db`), managed by `uv`
- **Frontend** — Next.js 16 / React 19 / TypeScript / Tailwind v4
- **LLM** — Anthropic Claude (`anthropic` SDK, Sonnet 4.6 default)
- **External APIs** — Google Places API (New), BBB.org scrape (`httpx` + `beautifulsoup4`)

## Commands

| | |
|---|---|
| `make setup` | Install backend + frontend deps, seed `backend/.env`, init SQLite schema |
| `make doctor` | Validate Anthropic + Google Places credentials against the live APIs |
| `make dev` | Run backend (:8000) + frontend (:3000) concurrently |
| `make backend` | Backend only |
| `make frontend` | Frontend only |
| `make test` | Backend tests (~80 pytest cases; Anthropic stubbed — no API calls) |
| `make clean` | Remove `.venv/`, `node_modules/`, `.next/`, and `tavi.db` |

## Layout

```
backend/                  FastAPI app + SQLite + LLM plumbing
  app/routers/              /intake, /discovery, /negotiations, /admin, /health
  app/services/             intake, discovery, negotiation (subpart 3 scheduler + agents)
  app/personas/pool/        8 markdown vendor archetypes for the simulator
  tests/                    pytest suite
frontend/                 Next.js app
  app/page.tsx              intake chat + address picker
  app/work-orders/[id]/     command center (subpart 3)
  app/admin/                read-only DB explorer
docs/                     hackathon brief + subpart specs + exported prompt history
CLAUDE.md                 architecture notes (load-bearing)
Makefile                  dev commands
```

## Environment variables

See [`backend/.env.example`](./backend/.env.example). Required: `ANTHROPIC_API_KEY`, `GOOGLE_PLACES_API_KEY`. Optional: `ANTHROPIC_MODEL`, `GOOGLE_PLACES_DEFAULT_RADIUS_M`, `CORS_ORIGINS`.

## Troubleshooting

- **`make setup` halts on "Node … is too old"** — Next.js 16 needs Node 20+. Upgrade via the path that matches your install: `brew upgrade node`, `nvm install 20 && nvm use 20`, or a fresh download from [nodejs.org](https://nodejs.org). Re-run `make setup` once Node is current.
- **`make setup` halts on "npm self-upgrade failed"** — permissions issue on the global Node dir. Retry with sudo: `sudo npm install -g npm@latest`. Then re-run `make setup`.
- **Google Places returns 403** — you likely enabled the legacy Places API. Enable **Places API (New)** specifically; legacy keys don't work against the new endpoints.
- **Anthropic 401 / 404 on model** — your key doesn't have access to `claude-sonnet-4-6`. Set `ANTHROPIC_MODEL` to a model your key supports.
- **Port 8000 or 3000 already in use** — `lsof -i :8000` (or `:3000`) to see what's holding it. Kill it, or change the port in `Makefile` + frontend config.
- **No vendors found** — discovery searches ~20 miles around the work-order address. Very rural addresses may come up empty; try an urban one.

## Testing

Backend tests live in `backend/tests/` and cover: hours-overlap edge cases, scoring math (Bayesian anchor, urgency profiles, quote-aware subjective ranking), BBB parser fixtures, coordinator tool dispatchers, pitch-template substitution, and the full scheduler flow (turn resolution, ghoster + refusal rolls, silence/confirmation/verification timeouts, sequential booking flow, cascade decline, readiness monotonicity). Anthropic is stubbed — `make test` makes no API calls and costs nothing.
