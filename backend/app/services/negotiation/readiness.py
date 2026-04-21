"""`ready_to_schedule` flag recomputation.

Isolated in its own module so both the scheduler's end-of-tick sweep and
the coordinator's per-tool-call hooks can share one authoritative
implementation — and to keep it out of scheduler.py / tools.py import cycles.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ...enums import TERMINAL_STATES, NegotiationState
from ...models import Negotiation, WorkOrder


# A negotiation counts as "done from the pre-quote funnel" when it's either
# given firm terms (QUOTED), already been resolved by winner-pick (SCHEDULED),
# or landed in any terminal state.
READY_TRIGGER_STATES: frozenset[NegotiationState] = TERMINAL_STATES | {
    NegotiationState.QUOTED,
    NegotiationState.SCHEDULED,
}


def refresh_ready_to_schedule(db: Session, work_order_id: str) -> bool:
    """Recompute and, if applicable, flip `WorkOrder.ready_to_schedule` to
    True for one work order.

    Condition: every non-filtered negotiation under the work order has a
    state in READY_TRIGGER_STATES (i.e., no negotiation is still actively
    pre-quote in PROSPECTING / CONTACTED / NEGOTIATING).

    Monotonic: once flipped to True, this function never flips it back.
    State transitions in the spec never move a negotiation out of a
    ready-trigger state into a non-trigger state, so this is safe in
    practice and keeps the flag predictable for downstream logic.

    Returns the current value after the check.
    """
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        return False

    if wo.ready_to_schedule:
        return True  # monotonic — nothing to do

    negs = (
        db.query(Negotiation)
        .filter(
            Negotiation.work_order_id == work_order_id,
            Negotiation.filtered == False,  # noqa: E712 — SQLAlchemy requires ==
        )
        .all()
    )
    if not negs:
        return False  # no non-filtered candidates; nothing to schedule yet

    if all(n.state in READY_TRIGGER_STATES for n in negs):
        wo.ready_to_schedule = True
        db.flush()
        return True

    return False
