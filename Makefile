.PHONY: help preflight check-node check-npm check-uv check-mailpit setup dev doctor backend frontend mailpit test clean

NODE_MIN := 20
NPM_MIN := 10

# Make uv findable in the same session if we auto-install it (uv installer puts the
# binary at ~/.local/bin/uv; that path isn't always on a bare-shell PATH).
export PATH := $(HOME)/.local/bin:$(PATH)

help:
	@echo "Tavi — dev commands"
	@echo ""
	@echo "  make setup     install deps, seed backend/.env, init SQLite"
	@echo "  make doctor    validate Anthropic + Google Places credentials against the live APIs"
	@echo "  make dev       run backend (:8000), frontend (:3000), and MailPit (:1025/:8025) concurrently"
	@echo "  make backend   backend only (uvicorn --reload on :8000)"
	@echo "  make frontend  frontend only (next dev on :3000)"
	@echo "  make mailpit   MailPit only (SMTP :1025, UI :8025) — the Tavi ↔ vendor email bus"
	@echo "  make test      backend tests (pytest, Anthropic stubbed)"
	@echo "  make clean     remove deps, build output, and the local DB"
	@echo ""
	@echo "setup and dev both run a preflight that checks Node ≥ $(NODE_MIN) and"
	@echo "npm ≥ $(NPM_MIN), auto-installs uv if missing, and auto-upgrades npm"
	@echo "if it's too old. Node itself cannot be auto-upgraded — instructions"
	@echo "are printed if your Node is too old. MailPit is optional but highly"
	@echo "recommended — the backend falls back to a degraded direct-DB path if"
	@echo "it's not running."

check-node:
	@if ! command -v node >/dev/null 2>&1; then \
		echo "error: Node is not installed. Next.js 16 requires Node $(NODE_MIN)+."; \
		echo ""; \
		echo "Install one of:"; \
		echo "  - Homebrew (macOS):   brew install node"; \
		echo "  - nvm:                nvm install $(NODE_MIN) && nvm use $(NODE_MIN)"; \
		echo "  - direct download:    https://nodejs.org"; \
		exit 1; \
	fi; \
	NODE_MAJOR=$$(node -v | sed 's/^v//' | cut -d. -f1); \
	if [ "$$NODE_MAJOR" -lt $(NODE_MIN) ]; then \
		echo "error: Node $$(node -v) is too old — Next.js 16 requires v$(NODE_MIN) or newer."; \
		echo ""; \
		echo "Upgrade one of:"; \
		echo "  - Homebrew:           brew upgrade node  (or: brew install node@$(NODE_MIN) && brew link --overwrite node@$(NODE_MIN))"; \
		echo "  - nvm:                nvm install $(NODE_MIN) && nvm use $(NODE_MIN) && nvm alias default $(NODE_MIN)"; \
		echo "  - direct download:    https://nodejs.org (pick v$(NODE_MIN) LTS or newer)"; \
		echo ""; \
		echo "Cannot auto-upgrade Node safely — too many install methods in use."; \
		exit 1; \
	fi; \
	echo "✓ Node $$(node -v)"

check-npm: check-node
	@if ! command -v npm >/dev/null 2>&1; then \
		echo "error: npm not found (normally bundled with Node)."; \
		exit 1; \
	fi; \
	NPM_MAJOR=$$(npm -v | cut -d. -f1); \
	if [ "$$NPM_MAJOR" -lt $(NPM_MIN) ]; then \
		echo "info: npm v$$(npm -v) is older than v$(NPM_MIN) — upgrading to latest..."; \
		if npm install -g npm@latest 2>&1; then \
			hash -r 2>/dev/null || true; \
			echo "✓ npm upgraded to $$(npm -v)"; \
		else \
			echo ""; \
			echo "error: npm self-upgrade failed — likely a permissions issue with your global Node dir."; \
			echo ""; \
			echo "Retry manually:"; \
			echo "  - nvm-managed Node:       npm install -g npm@latest"; \
			echo "  - Homebrew / system Node: sudo npm install -g npm@latest"; \
			echo "  - or reinstall Node (ships a fresh npm):  brew reinstall node"; \
			exit 1; \
		fi; \
	else \
		echo "✓ npm $$(npm -v)"; \
	fi

check-uv:
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "info: uv not found — installing via the official script..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh || { \
			echo ""; \
			echo "error: uv install failed."; \
			echo "Install manually: https://docs.astral.sh/uv/#installation"; \
			exit 1; \
		}; \
		if ! command -v uv >/dev/null 2>&1; then \
			echo ""; \
			echo "uv installed to ~/.local/bin but not yet on your shell's PATH."; \
			echo "This make run will pick it up (Makefile adds it). For future shells:"; \
			echo "  source ~/.local/bin/env   (or open a new terminal)"; \
		fi; \
		echo "✓ uv installed: $$(uv --version 2>/dev/null || echo 'at ~/.local/bin/uv')"; \
	else \
		echo "✓ uv $$(uv --version | awk '{print $$2}')"; \
	fi

# MailPit is the local SMTP + HTTP inbox used by subpart 3's agentic
# email bus. Soft requirement — if missing, print install guidance but
# don't fail. The backend falls back to direct DB writes when MailPit is
# unavailable, so the demo still works (just without visible emails).
check-mailpit:
	@if ! command -v mailpit >/dev/null 2>&1; then \
		echo "warn: mailpit not found — subpart 3 will run in DB-only fallback mode."; \
		echo ""; \
		echo "Install (strongly recommended — lets you watch the Tavi ↔ vendor"; \
		echo "email bus live in a browser at http://localhost:8025):"; \
		echo "  - Homebrew (macOS / Linux):  brew install mailpit"; \
		echo "  - Docker:                    docker run -d -p 8025:8025 -p 1025:1025 axllent/mailpit"; \
		echo "  - Binary:                    https://mailpit.axllent.org/docs/install/"; \
		echo ""; \
	else \
		echo "✓ mailpit installed ($$(which mailpit))"; \
	fi

preflight: check-node check-npm check-uv check-mailpit

setup: preflight
	@echo ""
	@echo "→ installing backend deps..."
	cd backend && uv sync
	@echo ""
	@echo "→ installing frontend deps..."
	cd frontend && npm install
	@if [ ! -f backend/.env ]; then \
		cp backend/.env.example backend/.env; \
		echo ""; \
		echo "→ created backend/.env — edit it to add your API keys before running 'make dev'"; \
	fi
	@echo ""
	@echo "→ initializing SQLite..."
	cd backend && uv run python create_db.py
	@echo ""
	@echo "setup complete. next:"
	@echo "  1. edit backend/.env with your Anthropic + Google Places keys"
	@echo "  2. run 'make dev'"

dev: preflight
	@test -d backend/.venv || { echo "error: backend deps missing. run 'make setup' first."; exit 1; }
	@test -d frontend/node_modules || { echo "error: frontend deps missing. run 'make setup' first."; exit 1; }
	@test -f backend/.env || { echo "error: backend/.env not found. run 'make setup' first."; exit 1; }
	@if grep -q '^ANTHROPIC_API_KEY=sk-ant-\.\.\.$$' backend/.env; then \
		echo "error: ANTHROPIC_API_KEY in backend/.env is still the placeholder. edit it with a real key."; exit 1; \
	fi
	@if grep -q '^GOOGLE_PLACES_API_KEY=$$' backend/.env; then \
		echo "error: GOOGLE_PLACES_API_KEY in backend/.env is empty. add a key."; exit 1; \
	fi
	@echo ""
	@if command -v mailpit >/dev/null 2>&1; then \
		echo "→ starting MailPit (:1025 SMTP, :8025 UI), backend (:8000), and frontend (:3000) — Ctrl-C to stop all"; \
		trap 'kill 0' INT TERM EXIT; \
			mailpit --quiet & \
			(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
			(cd frontend && npm run dev) & \
			wait; \
	else \
		echo "warn: mailpit not on PATH — running backend + frontend only. Subpart 3 will use the DB-only fallback."; \
		echo "→ starting backend on :8000 and frontend on :3000 — Ctrl-C to stop both"; \
		trap 'kill 0' INT TERM EXIT; \
			(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
			(cd frontend && npm run dev) & \
			wait; \
	fi

doctor:
	@test -f backend/.env || { echo "error: backend/.env not found. run 'make setup' first."; exit 1; }
	@test -d backend/.venv || { echo "error: backend deps missing. run 'make setup' first."; exit 1; }
	cd backend && uv run python doctor.py

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

mailpit:
	@command -v mailpit >/dev/null 2>&1 || { \
		echo "error: mailpit not installed."; \
		echo "Install: brew install mailpit  (or see https://mailpit.axllent.org/docs/install/)"; \
		exit 1; \
	}
	mailpit

test:
	cd backend && uv run pytest

clean:
	rm -rf backend/.venv backend/tavi.db backend/__pycache__
	rm -rf frontend/node_modules frontend/.next
