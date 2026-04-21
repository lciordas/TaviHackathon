"""Top-level scheduler for the negotiation loop.

One public entry point: `tick(db, work_order_id)`. Each call:
  1. Increments `WorkOrder.loop_iteration` by one.
  2. If winner-pick is ready (every non-filtered active negotiation is
     either QUOTED or terminal, and at least one is QUOTED), computes the
     per-quote accept/decline decisions and stashes them for this tick.
  3. Walks every non-filtered active negotiation and resolves whose turn it
     is per `Step 3.md` → *Whose turn is it?*. Invokes the coordinator or
     the vendor simulator accordingly. Vendor turns roll against the
     persona's skip probability before invoking the simulator.
  4. Commits and returns a TickResult for the command-center UI.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ...enums import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    MessageSender,
    NegotiationState,
)
from ...models import Negotiation, Vendor, WorkOrder
from ..discovery import scoring
from ..personas import skip_probability_for
from . import coordinator, messages, simulator


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class NegotiationEvent:
    negotiation_id: str
    vendor_place_id: str
    vendor_display_name: Optional[str]
    state_before: str
    state_after: str
    actor: str           # "tavi" | "vendor" | "none"
    outcome: str         # "message_sent" | "skipped" | "waiting" | "terminal" | "already_scheduled"
    message_id: Optional[str] = None
    detail: Optional[dict] = None


@dataclass
class WinnerPickResult:
    ranked: list[dict] = field(default_factory=list)  # {negotiation_id, vendor_display_name, rank, score, action}


@dataclass
class TickResult:
    work_order_id: str
    iteration: int
    events: list[NegotiationEvent]
    winner_pick: Optional[WinnerPickResult]


class SchedulerError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def tick(db: Session, work_order_id: str) -> TickResult:
    wo = db.get(WorkOrder, work_order_id)
    if wo is None:
        raise SchedulerError(f"WorkOrder not found: {work_order_id}")

    wo.loop_iteration = (wo.loop_iteration or 0) + 1
    iteration = wo.loop_iteration

    negs_all = _negotiations_for(db, work_order_id)
    negs = [n for n in negs_all if not n.filtered]
    vendors_by_id = _vendors_by_id(db, [n.vendor_place_id for n in negs])

    # Winner-pick: compute once at the top of the tick so QUOTED rows get a
    # quote_action injected into the coordinator's context on this same pass.
    quote_actions, winner_pick = _maybe_winner_pick(db, wo, negs, vendors_by_id)

    events: list[NegotiationEvent] = []

    for neg in negs:
        vendor = vendors_by_id.get(neg.vendor_place_id)
        if vendor is None:
            logger.warning("Vendor missing for negotiation %s — skipping", neg.id)
            continue

        state_before = neg.state
        event = _run_one(
            db,
            work_order=wo,
            negotiation=neg,
            vendor=vendor,
            iteration=iteration,
            quote_action=quote_actions.get(neg.id),
        )
        event.state_before = state_before.value
        event.state_after = neg.state.value
        event.vendor_display_name = vendor.display_name
        events.append(event)

    db.commit()

    return TickResult(
        work_order_id=work_order_id,
        iteration=iteration,
        events=events,
        winner_pick=winner_pick,
    )


# ---------------------------------------------------------------------------
# Per-negotiation dispatch
# ---------------------------------------------------------------------------

def _run_one(
    db: Session,
    *,
    work_order: WorkOrder,
    negotiation: Negotiation,
    vendor: Vendor,
    iteration: int,
    quote_action: Optional[str],
) -> NegotiationEvent:
    state = negotiation.state

    base = NegotiationEvent(
        negotiation_id=negotiation.id,
        vendor_place_id=vendor.place_id,
        vendor_display_name=None,  # filled by caller
        state_before=state.value,
        state_after=state.value,
        actor="none",
        outcome="waiting",
    )

    if state in TERMINAL_STATES:
        base.outcome = "terminal"
        return base

    if state == NegotiationState.SCHEDULED:
        # Awaiting external completion / noshow signal from the operator.
        base.outcome = "already_scheduled"
        return base

    if state == NegotiationState.QUOTED:
        # Coordinator acts only when winner-pick has decided something for
        # this neg; otherwise we wait for peer negotiations to reach QUOTED.
        if quote_action is None:
            base.outcome = "waiting"
            return base
        result = coordinator.run_turn(
            db,
            negotiation=negotiation,
            work_order=work_order,
            vendor=vendor,
            iteration=iteration,
            quote_action=quote_action,
        )
        base.actor = "tavi"
        base.outcome = "message_sent"
        base.message_id = result["message_id"]
        base.detail = {"quote_action": quote_action}
        return base

    if state == NegotiationState.PROSPECTING:
        result = coordinator.run_turn(
            db,
            negotiation=negotiation,
            work_order=work_order,
            vendor=vendor,
            iteration=iteration,
        )
        base.actor = "tavi"
        base.outcome = "message_sent"
        base.message_id = result["message_id"]
        return base

    if state == NegotiationState.CONTACTED:
        # Vendor's first reply. Roll skip.
        if _vendor_skips(vendor):
            base.actor = "vendor"
            base.outcome = "skipped"
            return base
        result = simulator.run_turn(
            db,
            negotiation=negotiation,
            work_order=work_order,
            vendor=vendor,
            iteration=iteration,
        )
        base.actor = "vendor"
        base.outcome = "message_sent"
        base.message_id = result["message_id"]
        return base

    if state == NegotiationState.NEGOTIATING:
        last = messages.last_message(db, negotiation.id)
        # NEGOTIATING with no messages is an invariant violation per spec;
        # treat it as the coordinator's turn so the flow self-heals.
        last_sender = last.sender if last is not None else MessageSender.VENDOR
        if last_sender == MessageSender.VENDOR:
            result = coordinator.run_turn(
                db,
                negotiation=negotiation,
                work_order=work_order,
                vendor=vendor,
                iteration=iteration,
            )
            base.actor = "tavi"
            base.outcome = "message_sent"
            base.message_id = result["message_id"]
            return base
        # Last was TAVI — vendor's turn.
        if _vendor_skips(vendor):
            base.actor = "vendor"
            base.outcome = "skipped"
            return base
        result = simulator.run_turn(
            db,
            negotiation=negotiation,
            work_order=work_order,
            vendor=vendor,
            iteration=iteration,
        )
        base.actor = "vendor"
        base.outcome = "message_sent"
        base.message_id = result["message_id"]
        return base

    # Defensive default.
    return base


def _vendor_skips(vendor: Vendor) -> bool:
    return random.random() < skip_probability_for(vendor.persona_markdown)


# ---------------------------------------------------------------------------
# Winner-pick
# ---------------------------------------------------------------------------

def _maybe_winner_pick(
    db: Session,
    work_order: WorkOrder,
    negotiations: list[Negotiation],
    vendors_by_id: dict[str, Vendor],
) -> tuple[dict[str, str], Optional[WinnerPickResult]]:
    """If every active non-filtered negotiation has quoted or terminated and
    at least one is in QUOTED, compute the accept/decline decisions.

    Returns (quote_actions, winner_pick). `quote_actions` maps negotiation_id
    → "accept" | "decline" for consumption by the coordinator on this tick.
    """
    active = [n for n in negotiations if n.state in ACTIVE_STATES]
    quoted = [n for n in active if n.state == NegotiationState.QUOTED]
    still_open = [n for n in active if n.state not in (NegotiationState.QUOTED, NegotiationState.SCHEDULED)]

    if not quoted:
        return {}, None
    if still_open:
        return {}, None  # wait for stragglers to quote or terminate

    # Rank by subjective score under the work order's urgency-default weights.
    weights = scoring.default_weights_for(work_order.urgency)
    ranked_rows: list[tuple[Negotiation, float]] = []
    for neg in quoted:
        vendor = vendors_by_id.get(neg.vendor_place_id)
        if vendor is None:
            continue
        result = scoring.compute_subjective(
            cumulative_score=vendor.cumulative_score or 0.0,
            quote_cents=neg.quoted_price_cents or 0,
            budget_cap_cents=work_order.budget_cap_cents,
            weights=weights,
        )
        neg.subjective_rank_score = result.score
        neg.subjective_rank_breakdown = result.breakdown
        ranked_rows.append((neg, result.score))

    ranked_rows.sort(key=lambda x: x[1], reverse=True)

    quote_actions: dict[str, str] = {}
    picked: list[dict] = []
    for rank_idx, (neg, score) in enumerate(ranked_rows, start=1):
        neg.rank = rank_idx
        action = "accept" if rank_idx == 1 else "decline"
        quote_actions[neg.id] = action
        vendor = vendors_by_id.get(neg.vendor_place_id)
        picked.append({
            "negotiation_id": neg.id,
            "vendor_display_name": vendor.display_name if vendor else None,
            "rank": rank_idx,
            "score": score,
            "action": action,
        })

    db.flush()
    return quote_actions, WinnerPickResult(ranked=picked)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _negotiations_for(db: Session, work_order_id: str) -> list[Negotiation]:
    return (
        db.query(Negotiation)
        .filter(Negotiation.work_order_id == work_order_id)
        .order_by(Negotiation.created_at.asc())
        .all()
    )


def _vendors_by_id(db: Session, place_ids: list[str]) -> dict[str, Vendor]:
    if not place_ids:
        return {}
    rows = db.query(Vendor).filter(Vendor.place_id.in_(place_ids)).all()
    return {v.place_id: v for v in rows}
