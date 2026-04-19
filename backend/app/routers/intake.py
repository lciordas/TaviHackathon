"""Intake API routes."""

import logging

from anthropic import APIError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..agent import run_turn
from ..database import get_db
from ..prompts import GREETING
from ..schemas import (
    IntakeConfirmRequest,
    IntakeConfirmResponse,
    IntakeStartResponse,
    IntakeTurnRequest,
    IntakeTurnResponse,
    WorkOrderPartial,
    WorkOrderRead,
)
from ..services.intake import MissingFieldsError, create_work_order

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/start", response_model=IntakeStartResponse)
def start() -> IntakeStartResponse:
    return IntakeStartResponse(greeting=GREETING, fields=WorkOrderPartial())


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
    db: Session = Depends(get_db),
) -> IntakeConfirmResponse:
    try:
        work_order = create_work_order(db, req.fields)
    except MissingFieldsError as exc:
        raise HTTPException(status_code=400, detail={"missing": exc.missing})

    return IntakeConfirmResponse(
        id=work_order.id,
        work_order=WorkOrderRead.model_validate(work_order),
    )
