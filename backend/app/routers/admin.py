"""Read-only DB explorer endpoints for the admin UI.

No auth — hackathon mode. Each endpoint returns a list of rows sorted for
display (most-relevant first). Negotiations are joined with the vendor's
display_name so the frontend doesn't have to cross-reference IDs, and their
full message threads are eagerly embedded.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DiscoveryRun, Negotiation, NegotiationMessage, Vendor, WorkOrder
from ..schemas import (
    AdminNegotiationRead,
    AdminOverview,
    AdminTableCounts,
    DiscoveryRunRead,
    NegotiationMessageRead,
    VendorRead,
    WorkOrderRead,
)


router = APIRouter()


@router.get("/overview", response_model=AdminOverview)
def overview(db: Session = Depends(get_db)) -> AdminOverview:
    return AdminOverview(
        counts=AdminTableCounts(
            work_orders=db.query(WorkOrder).count(),
            vendors=db.query(Vendor).count(),
            discovery_runs=db.query(DiscoveryRun).count(),
            negotiations=db.query(Negotiation).count(),
            negotiation_messages=db.query(NegotiationMessage).count(),
        ),
    )


@router.get("/work_orders", response_model=list[WorkOrderRead])
def list_work_orders(db: Session = Depends(get_db)) -> list[WorkOrderRead]:
    rows = db.query(WorkOrder).order_by(WorkOrder.created_at.desc()).all()
    return [WorkOrderRead.model_validate(r) for r in rows]


@router.get("/vendors", response_model=list[VendorRead])
def list_vendors(db: Session = Depends(get_db)) -> list[VendorRead]:
    rows = (
        db.query(Vendor)
        .order_by(Vendor.cumulative_score.desc().nulls_last(), Vendor.display_name.asc())
        .all()
    )
    return [VendorRead.model_validate(r) for r in rows]


@router.get("/discovery_runs", response_model=list[DiscoveryRunRead])
def list_discovery_runs(db: Session = Depends(get_db)) -> list[DiscoveryRunRead]:
    rows = db.query(DiscoveryRun).order_by(DiscoveryRun.created_at.desc()).all()
    return [DiscoveryRunRead.model_validate(r) for r in rows]


@router.get("/negotiations", response_model=list[AdminNegotiationRead])
def list_negotiations(db: Session = Depends(get_db)) -> list[AdminNegotiationRead]:
    """Negotiations joined with vendor + eagerly embedded message threads.

    Sort: most recent discovery run first, survivors before filtered, then
    vendor quality desc (pre-quote proxy — once quotes arrive, subjective
    rank supersedes it).
    """
    rows = (
        db.query(Negotiation, Vendor.display_name, Vendor.cumulative_score)
        .join(Vendor, Vendor.place_id == Negotiation.vendor_place_id)
        .order_by(
            Negotiation.created_at.desc(),
            Negotiation.filtered.asc(),
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
