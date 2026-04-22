"""Intake API routes."""

import json
import logging
from pathlib import Path

from anthropic import APIError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..agent import run_turn
from ..database import SessionLocal, get_db
from ..prompts import GREETING
from ..schemas import (
    IntakeConfirmRequest,
    IntakeConfirmResponse,
    IntakeScenario,
    IntakeStartResponse,
    IntakeTurnRequest,
    IntakeTurnResponse,
    WorkOrderPartial,
    WorkOrderRead,
)
from ..services.discovery.orchestrator import DiscoveryError, run_discovery
from ..services.intake import MissingFieldsError, create_work_order

logger = logging.getLogger(__name__)

router = APIRouter()

# backend/app/routers/intake.py → parents[3] = repo root
_SCENARIOS_FILE = (
    Path(__file__).resolve().parents[3]
    / "vendor-discovery"
    / "data"
    / "seed"
    / "requests.json"
)


@router.post("/start", response_model=IntakeStartResponse)
def start() -> IntakeStartResponse:
    return IntakeStartResponse(greeting=GREETING, fields=WorkOrderPartial())


@router.get("/scenarios", response_model=list[IntakeScenario])
def scenarios() -> list[IntakeScenario]:
    """Pre-built demo scenarios from requests.json for one-click intake.

    Empty list if the seed file is absent (e.g., fresh clone without the
    vendor-discovery subtree) — the frontend just hides the panel in that case.
    """
    if not _SCENARIOS_FILE.exists():
        return []
    try:
        with open(_SCENARIOS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("couldn't parse scenarios file %s: %s", _SCENARIOS_FILE, exc)
        return []

    out: list[IntakeScenario] = []
    for req in data.get("requests", []):
        message = (req.get("chat") or {}).get("message")
        if not message:
            continue
        wo = req.get("work_order") or {}
        out.append(
            IntakeScenario(
                id=req.get("request_id") or f"req_{len(out)+1}",
                trade=wo.get("trade"),
                urgency=wo.get("urgency"),
                city=wo.get("city"),
                message=message,
            )
        )
    return out


@router.post("/chat", response_model=IntakeTurnResponse)
def chat(req: IntakeTurnRequest) -> IntakeTurnResponse:
    try:
        reply, fields, is_ready, missing = run_turn(req.messages, req.fields)
    except APIError as exc:
        logger.exception("Anthropic API error during intake chat")
        raise HTTPException(status_code=502, detail=f"LLM unavailable: {exc}")

    return IntakeTurnResponse(
        reply=reply,
        fields=fields,
        is_ready=is_ready,
        missing=missing,
    )


@router.post("/confirm", response_model=IntakeConfirmResponse)
def confirm(
    req: IntakeConfirmRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> IntakeConfirmResponse:
    try:
        work_order = create_work_order(db, req.fields)
    except MissingFieldsError as exc:
        raise HTTPException(status_code=400, detail={"missing": exc.missing})

    # Phase 2 kicks off automatically. FastAPI runs this after the response is
    # sent, in a worker thread (run_discovery is sync + does blocking I/O).
    # Failures here never break intake — they're logged and dropped.
    background_tasks.add_task(_run_discovery_in_background, work_order.id)

    return IntakeConfirmResponse(
        id=work_order.id,
        work_order=WorkOrderRead.model_validate(work_order),
    )


def _run_discovery_in_background(work_order_id: str) -> None:
    """Spawn a fresh DB session and run vendor discovery.

    The request-scoped session from `get_db` is closed by the time the
    background task runs, so we open our own.
    """
    db = SessionLocal()
    try:
        run = run_discovery(db, work_order_id, refresh=False)
        logger.info(
            "Background discovery completed for %s: run=%s, candidates=%d, api_calls=%d, duration_ms=%s",
            work_order_id,
            run.id,
            run.candidate_count,
            run.api_detail_calls,
            run.duration_ms,
        )
    except DiscoveryError as e:
        logger.warning("Background discovery failed for %s: %s", work_order_id, e)
    except Exception:
        logger.exception("Unexpected error in background discovery for %s", work_order_id)
    finally:
        db.close()
