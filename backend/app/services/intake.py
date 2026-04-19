"""Work order persistence."""

from sqlalchemy.orm import Session

from ..models import WorkOrder
from ..schemas import REQUIRED_FIELDS, WorkOrderPartial


class MissingFieldsError(ValueError):
    """Raised when a WorkOrderPartial is missing required fields for persistence."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"missing required fields: {missing}")


def create_work_order(db: Session, fields: WorkOrderPartial) -> WorkOrder:
    """Validate required fields are present and insert a WorkOrder row."""
    missing = [f for f in REQUIRED_FIELDS if getattr(fields, f) is None]
    if missing:
        raise MissingFieldsError(missing)

    work_order = WorkOrder(
        trade=fields.trade,
        description=fields.description,
        address_line=fields.address_line,
        city=fields.city,
        state=fields.state,
        zip=fields.zip,
        lat=fields.lat,
        lng=fields.lng,
        access_notes=fields.access_notes,
        urgency=fields.urgency,
        scheduled_for=fields.scheduled_for,
        budget_cap_cents=fields.budget_cap_cents,
        quality_threshold=fields.quality_threshold,
        requires_licensed=fields.requires_licensed,
        requires_insured=fields.requires_insured,
    )
    db.add(work_order)
    db.commit()
    db.refresh(work_order)
    return work_order
