"""Negotiation scheduler routes.

One canonical entry point for the demo: `POST /negotiations/tick` advances
the per-work-order loop by one iteration. The command center calls this
when the operator presses the Tick button.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Negotiation, NegotiationMessage, Vendor, WorkOrder
from ..schemas import (
    AdminNegotiationRead,
    NegotiationEventRead,
    NegotiationMessageRead,
    TickRequest,
    TickResponse,
    WinnerPickRead,
    WorkOrderRead,
)
from ..services.negotiation.scheduler import (
    SchedulerError,
    TickResult,
    tick,
)


logger = logging.getLogger(__name__)


router = APIRouter()


@router.post("/tick", response_model=TickResponse)
def run_tick(req: TickRequest, db: Session = Depends(get_db)) -> TickResponse:
    try:
        result = tick(db, req.work_order_id)
    except SchedulerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(result)


@router.get("/by_work_order/{work_order_id}", response_model=list[AdminNegotiationRead])
def list_by_work_order(
    work_order_id: str, db: Session = Depends(get_db)
) -> list[AdminNegotiationRead]:
    """All negotiations for one work order, hydrated with vendor + thread.

    The command center uses this to hydrate the kanban board. Includes
    filtered negotiations too — the UI can show them in a collapsed
    "excluded at discovery" bucket.
    """
    rows = (
        db.query(Negotiation, Vendor.display_name, Vendor.cumulative_score)
        .join(Vendor, Vendor.place_id == Negotiation.vendor_place_id)
        .filter(Negotiation.work_order_id == work_order_id)
        .order_by(
            Negotiation.filtered.asc(),
            Negotiation.rank.asc().nulls_last(),
            Vendor.cumulative_score.desc().nulls_last(),
        )
        .all()
    )
    if not rows:
        return []

    neg_ids = [n.id for n, _, _ in rows]
    msg_rows = (
        db.query(NegotiationMessage)
        .filter(NegotiationMessage.negotiation_id.in_(neg_ids))
        .order_by(
            NegotiationMessage.negotiation_id,
            NegotiationMessage.iteration.asc(),
            NegotiationMessage.created_at.asc(),
        )
        .all()
    )
    by_neg: dict[str, list[NegotiationMessageRead]] = defaultdict(list)
    for m in msg_rows:
        by_neg[m.negotiation_id].append(NegotiationMessageRead.model_validate(m))

    out: list[AdminNegotiationRead] = []
    for neg, vendor_name, vendor_cum in rows:
        read = AdminNegotiationRead.model_validate(neg)
        read.vendor_display_name = vendor_name
        read.vendor_cumulative_score = vendor_cum
        read.messages = by_neg.get(neg.id, [])
        out.append(read)
    return out


@router.get("/work_order/{work_order_id}", response_model=WorkOrderRead)
def get_work_order(work_order_id: str, db: Session = Depends(get_db)) -> WorkOrderRead:
    """Fetch one work order (for iteration counter + context in the command center)."""
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="WorkOrder not found")
    return WorkOrderRead.model_validate(wo)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_response(result: TickResult) -> TickResponse:
    events = [
        NegotiationEventRead(
            negotiation_id=e.negotiation_id,
            vendor_place_id=e.vendor_place_id,
            vendor_display_name=e.vendor_display_name,
            state_before=e.state_before,
            state_after=e.state_after,
            actor=e.actor,
            outcome=e.outcome,
            message_id=e.message_id,
            detail=e.detail,
        )
        for e in result.events
    ]
    winner = WinnerPickRead(ranked=result.winner_pick.ranked) if result.winner_pick else None
    return TickResponse(
        work_order_id=result.work_order_id,
        iteration=result.iteration,
        events=events,
        winner_pick=winner,
    )
