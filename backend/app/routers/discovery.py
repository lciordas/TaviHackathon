"""Vendor-discovery API routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DiscoveryEvent, DiscoveryRun, Negotiation, Vendor
from ..schemas import (
    DiscoveryEventRead,
    DiscoveryRunRead,
    DiscoveryRunRequest,
    DiscoveryRunResponse,
    NegotiationRead,
    RankedVendor,
    VendorRead,
)
from ..services.discovery.orchestrator import DiscoveryError, run_discovery


logger = logging.getLogger(__name__)

router = APIRouter()


def _hydrate(db: Session, run: DiscoveryRun) -> DiscoveryRunResponse:
    # Pre-subpart-3 the `rank` column is null for every survivor. Order by
    # vendor quality as a quality-first proxy; once subpart 3 fills in
    # subjective_rank_score and rank, this should prefer rank asc.
    rows = (
        db.query(Negotiation, Vendor)
        .join(Vendor, Vendor.place_id == Negotiation.vendor_place_id)
        .filter(Negotiation.discovery_run_id == run.id)
        .order_by(
            Negotiation.filtered.asc(),
            Negotiation.rank.asc().nulls_last(),
            Vendor.cumulative_score.desc().nulls_last(),
        )
        .all()
    )
    ranked: list[RankedVendor] = []
    filtered: list[RankedVendor] = []
    for n, v in rows:
        entry = RankedVendor(
            negotiation=NegotiationRead.model_validate(n),
            vendor=VendorRead.model_validate(v),
        )
        if n.filtered:
            filtered.append(entry)
        else:
            ranked.append(entry)
    return DiscoveryRunResponse(
        run=DiscoveryRunRead.model_validate(run),
        ranked=ranked,
        filtered=filtered,
    )


@router.post("/run", response_model=DiscoveryRunResponse)
def run(req: DiscoveryRunRequest, db: Session = Depends(get_db)) -> DiscoveryRunResponse:
    try:
        run_row = run_discovery(db, req.work_order_id, refresh=req.refresh)
    except DiscoveryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _hydrate(db, run_row)


@router.get("/run/{run_id}", response_model=DiscoveryRunResponse)
def get_run(run_id: str, db: Session = Depends(get_db)) -> DiscoveryRunResponse:
    run_row = db.get(DiscoveryRun, run_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail="DiscoveryRun not found")
    return _hydrate(db, run_row)


@router.get(
    "/events/by_work_order/{work_order_id}",
    response_model=list[DiscoveryEventRead],
)
def events_by_work_order(
    work_order_id: str,
    db: Session = Depends(get_db),
) -> list[DiscoveryEventRead]:
    """Chronological list of discovery progress events for a work order.

    Used by the command-center activity log to stream real-time progress
    while the background discovery task is running. Returns [] if no events
    exist yet.
    """
    rows = (
        db.query(DiscoveryEvent)
        .filter(DiscoveryEvent.work_order_id == work_order_id)
        .order_by(DiscoveryEvent.created_at.asc())
        .all()
    )
    return [DiscoveryEventRead.model_validate(r) for r in rows]
