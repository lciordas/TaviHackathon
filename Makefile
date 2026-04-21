.PHONY: help setup dev backend frontend test clean

help:
	@echo "Tavi — dev commands"
	@echo ""
	@echo "  make setup     install backend + frontend deps, create backend/.env, init SQLite"
	@echo "  make dev       run backend (:8000) and frontend (:3000) concurrently"
	@echo "  make backend   backend only (uvicorn --reload on :8000)"
	@echo "  make frontend  frontend only (next dev on :3000)"
	@echo "  make test      backend tests (pytest, Anthropic stubbed)"
	@echo "  make clean     remove deps, build output, and the local DB"

setup:
	@command -v uv >/dev/null 2>&1 || { echo "error: uv not installed. install with: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "error: npm not installed. install Node 20+ from nodejs.org"; exit 1; }
	cd backend && uv sync
	cd frontend && npm install
	@if [ ! -f backend/.env ]; then \
		cp backend/.env.example backend/.env; \
		echo ""; \
		echo "created backend/.env — edit it to add your API keys before running 'make dev'"; \
	fi
	cd backend && uv run python create_db.py
	@echo ""
	@echo "setup complete. next:"
	@echo "  1. edit backend/.env with your Anthropic + Google Places keys"
	@echo "  2. run 'make dev'"

dev:
	@test -f backend/.env || { echo "error: backend/.env not found. run 'make setup' first."; exit 1; }
	@if grep -q '^ANTHROPIC_API_KEY=sk-ant-\.\.\.$$' backend/.env; then \
		echo "error: ANTHROPIC_API_KEY in backend/.env is still the placeholder. edit it with a real key."; exit 1; \
	fi
	@if grep -q '^GOOGLE_PLACES_API_KEY=$$' backend/.env; then \
		echo "error: GOOGLE_PLACES_API_KEY in backend/.env is empty. add a key."; exit 1; \
	fi
	@echo "starting backend on :8000 and frontend on :3000 — Ctrl-C to stop both"
	@trap 'kill 0' INT TERM EXIT; \
		(cd backend && uv run uvicorn app.main:app --reload --port 8000) & \
		(cd frontend && npm run dev) & \
		wait

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest

clean:
	rm -rf backend/.venv backend/tavi.db backend/__pycache__
	rm -rf frontend/node_modules frontend/.next
