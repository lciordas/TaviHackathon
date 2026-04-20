"""Read-only DB explorer endpoints for the admin UI.

No auth — hackathon mode. Each endpoint returns a list of rows sorted for
display (most-relevant first). Negotiations are joined with the vendor's
display_name so the frontend doesn't have to cross-reference IDs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DiscoveryRun, Negotiation, Vendor, WorkOrder
from ..schemas import (
    AdminNegotiationRead,
    AdminOverview,
    AdminTableCounts,
    DiscoveryRunRead,
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
    """Join negotiations with the vendor's display_name for readability.

    Sort: most recent discovery_run first, then rank ascending (filtered rows last).
    """
    rows = (
        db.query(Negotiation, Vendor.display_name)
        .join(Vendor, Vendor.place_id == Negotiation.vendor_place_id)
        .order_by(
            Negotiation.created_at.desc(),
            Negotiation.filtered.asc(),
            Negotiation.rank.asc().nulls_last(),
        )
        .all()
    )
    out: list[AdminNegotiationRead] = []
    for neg, vendor_name in rows:
        read = AdminNegotiationRead.model_validate(neg)
        read.vendor_display_name = vendor_name
        out.append(read)
    return out
