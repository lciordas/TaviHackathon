"""Top-level scheduler for the negotiation loop.

One public entry point: `tick(db, work_order_id)`. Each call:
  1. Increments `WorkOrder.loop_iteration` by one.
  2. Refreshes subjective ranks across every QUOTED/SCHEDULED negotiation so
     the command center shows a live leaderboard.
  3. Walks every non-filtered active negotiation and resolves whose turn it
     is per `Step 3.md` → *Whose turn is it?*:
       - PROSPECTING / CONTACTED / NEGOTIATING are the pre-quote funnel.
         Vendor-silence timeout (SILENCE_TIMEOUT_TICKS) force-declines any
         negotiation where it's the vendor's turn and they've stayed silent.
       - QUOTED splits by `WorkOrder.ready_to_schedule`:
           * not ready → silent (waiting for peers to catch up)
           * ready     → the lowest-rank QUOTED neg is the "active pick".
                         First the coordinator sends a booking confirmation
                         request; then we wait for the vendor to reply (up
                         to CONFIRMATION_TIMEOUT_TICKS) and let the
                         coordinator accept/decline on the reply. Timeouts
                         force-decline so the next rank can become the pick.
       - SCHEDULED / terminal — skipped.
  4. If any QUOTED neg transitioned to SCHEDULED this tick, the remaining
     QUOTED peers get force-declined as "another vendor was booked".
  5. Commits and returns a TickResult for the command-center UI.
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
    MessageChannel,
    MessageSender,
    NegotiationState,
)
from ...models import Negotiation, Vendor, WorkOrder
from ..discovery import scoring
from ..personas import skip_probability_for
from . import coordinator, messages, simulator
from .readiness import refresh_ready_to_schedule


logger = logging.getLogger(__name__)


# Scheduler-driven termination thresholds (see docs/Step 3.md → top loop).
SILENCE_TIMEOUT_TICKS = 3        # vendor silent during pre-quote negotiation
CONFIRMATION_TIMEOUT_TICKS = 2   # vendor silent after a booking-confirmation request


# Ghoster probability: some vendors never reply to the initial Tavi pitch.
# Weighted inversely against vendor quality — a perfect-score vendor is
# rarely a ghoster; a 0-score vendor ghosts often. Applies once per (work
# order × vendor) at the CONTACTED state's first vendor turn; the result is
# persisted on `Negotiation.attributes.is_ghoster` so the roll is stable.
GHOST_PROB_MAX = 0.35  # applied at cumulative_score = 0
GHOST_PROB_MIN = 0.05  # applied at cumulative_score = 1

# Refusal probability: a slim chance the vendor politely declines the
# opportunity outright. Weighted POSITIVELY to quality — higher-ranked shops
# are in higher demand and turn work down more often. Rolled once on the
# first vendor turn in CONTACTED (after the ghoster check) and persisted as
# `Negotiation.attributes.refused` to avoid re-rolling.
REFUSE_PROB_MIN = 0.05  # applied at cumulative_score = 0
REFUSE_PROB_MAX = 0.15  # applied at cumulative_score = 1


REFUSAL_MESSAGES: tuple[str, ...] = (
    "Appreciate the outreach, but we're not taking on new work right now.",
    "Thanks for reaching out — unfortunately this isn't a fit for us.",
    "We're booked solid for the next few weeks, so we'll have to pass on this one.",
    "Thanks, but our schedule is full through that window — can't commit.",
    "Appreciate it, but we'll have to pass on this job.",
)


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
    actor: str           # "tavi" | "vendor" | "system" | "none"
    outcome: str         # see STATE_OUTCOMES below
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
    # Commit the iteration bump immediately so the command center (polling
    # while the tick runs) can show "iteration N" before the per-neg work
    # starts streaming in.
    db.commit()

    negs_all = _negotiations_for(db, work_order_id)
    negs = [n for n in negs_all if not n.filtered]
    vendors_by_id = _vendors_by_id(db, [n.vendor_place_id for n in negs])

    # Refresh subjective ranks across every currently-quoted neg so the
    # command center shows a live leaderboard as quotes come in. Idempotent.
    _refresh_quoted_ranks(db, wo, negs, vendors_by_id)
    db.commit()

    # Identify the "active pick" for the booking-confirmation flow: the
    # lowest-rank QUOTED neg. Only meaningful when ready_to_schedule is True;
    # every other QUOTED neg stays silent until this one resolves.
    active_pick_id = _active_pick_id(wo, negs) if wo.ready_to_schedule else None

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
            active_pick_id=active_pick_id,
        )
        event.state_before = state_before.value
        event.state_after = neg.state.value
        event.vendor_display_name = vendor.display_name
        events.append(event)

        # Commit after each negotiation's dispatch so a polling UI (command
        # center) sees each message the moment it's written — not all at
        # once when the full tick completes. For an 8-vendor work order
        # with LLM-backed agents, this turns a ~15s blank wait into a
        # stream of updates.
        db.commit()

    # If anyone became SCHEDULED this tick, the auction is over — decline the
    # remaining QUOTED peers. Safe to run every tick (idempotent).
    _cascade_decline_on_scheduled(negs)

    # End-of-tick sweep: recompute the readiness flag. Redundant with the
    # per-tool-call check but catches direct state mutations (timeouts).
    refresh_ready_to_schedule(db, work_order_id)

    db.commit()

    return TickResult(
        work_order_id=work_order_id,
        iteration=iteration,
        events=events,
        winner_pick=None,  # legacy field — superseded by the live rank + booking flow
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
    active_pick_id: Optional[str],
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
        return _run_quoted(
            db, work_order=work_order, negotiation=negotiation, vendor=vendor,
            iteration=iteration, active_pick_id=active_pick_id, base=base,
        )

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

    if state in (NegotiationState.CONTACTED, NegotiationState.NEGOTIATING):
        return _run_pre_quote(
            db, work_order=work_order, negotiation=negotiation, vendor=vendor,
            iteration=iteration, base=base,
        )

    return base


def _run_pre_quote(
    db: Session,
    *,
    work_order: WorkOrder,
    negotiation: Negotiation,
    vendor: Vendor,
    iteration: int,
    base: NegotiationEvent,
) -> NegotiationEvent:
    """CONTACTED / NEGOTIATING handling.

    Whose turn: CONTACTED → vendor (first reply). NEGOTIATING → whoever
    didn't send the last message. Vendor-silence timeout applies in both
    cases when it's the vendor's turn.
    """
    last = messages.last_message(db, negotiation.id)
    last_sender = last.sender if last is not None else MessageSender.VENDOR

    # CONTACTED always means "waiting for vendor's first reply". For
    # NEGOTIATING, vendor's turn iff the last message was from Tavi.
    vendors_turn = (
        negotiation.state == NegotiationState.CONTACTED
        or last_sender == MessageSender.TAVI
    )

    if vendors_turn:
        # Check silence timeout first.
        if last is not None and last.sender == MessageSender.TAVI:
            iters_since = iteration - last.iteration
            if iters_since >= SILENCE_TIMEOUT_TICKS:
                _force_decline(
                    negotiation,
                    reason=f"no response within {iters_since} ticks",
                )
                base.actor = "system"
                base.outcome = "silence_timeout"
                base.detail = {"ticks_since_last_tavi": iters_since}
                return base

        # Vendor's turn, within the window. Roll skip (ghoster + persona).
        if _vendor_skips(negotiation, vendor):
            base.actor = "vendor"
            base.outcome = "skipped"
            return base

        # Refusal — only at first reply (CONTACTED). Vendor posts one polite
        # decline message and the negotiation terminates immediately.
        refusal = _roll_refusal(negotiation, vendor)
        if refusal is not None:
            msg = messages.append_message(
                db, negotiation,
                sender=MessageSender.VENDOR, channel=MessageChannel.EMAIL,
                iteration=iteration, content={"text": refusal},
            )
            _force_decline(negotiation, reason="vendor declined the opportunity")
            base.actor = "vendor"
            base.outcome = "refused"
            base.message_id = msg.id
            return base

        result = simulator.run_turn(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
        )
        base.actor = "vendor"
        base.outcome = "message_sent"
        base.message_id = result["message_id"]
        return base

    # Tavi's turn (NEGOTIATING with last vendor reply).
    result = coordinator.run_turn(
        db, negotiation=negotiation, work_order=work_order,
        vendor=vendor, iteration=iteration,
    )
    base.actor = "tavi"
    base.outcome = "message_sent"
    base.message_id = result["message_id"]
    return base


def _run_quoted(
    db: Session,
    *,
    work_order: WorkOrder,
    negotiation: Negotiation,
    vendor: Vendor,
    iteration: int,
    active_pick_id: Optional[str],
    base: NegotiationEvent,
) -> NegotiationEvent:
    """QUOTED handling — sequential credential-verify + booking-confirm flow.

    Pre-ready (`WorkOrder.ready_to_schedule=false`): silent. Wait for peers
    to catch up or terminate.

    Post-ready, only the "active pick" (lowest-rank QUOTED neg) moves each
    tick. Others stay queued. The active pick runs through two sub-phases:

      1. Credential verification. If the work order requires licensed /
         insured and those facts aren't on the negotiation yet, Tavi
         conducts a focused Q&A until insurance_verified / license_verified
         are recorded (or the vendor refuses → decline_quote, or times out
         on silence).
      2. Booking confirmation. Same as before — request, vendor reply,
         accept_quote / decline_quote.
    """
    if not work_order.ready_to_schedule:
        base.outcome = "waiting"
        return base

    if active_pick_id is None or negotiation.id != active_pick_id:
        # Post-ready but we're not the current pick — queued behind a
        # higher-ranked vendor going through verification / confirmation.
        base.outcome = "queued"
        return base

    # Gate: credentials first, booking confirmation second.
    if not _credentials_verified(work_order, negotiation):
        return _run_verification(
            db, work_order=work_order, negotiation=negotiation,
            vendor=vendor, iteration=iteration, base=base,
        )

    attrs = dict(negotiation.attributes or {})
    sent_at = attrs.get("booking_confirmation_requested_at_iteration")

    if sent_at is None:
        # First time selected. Coordinator sends the booking confirmation.
        result = coordinator.run_turn(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
            quote_action="request_confirmation",
        )
        attrs["booking_confirmation_requested_at_iteration"] = iteration
        negotiation.attributes = attrs
        db.flush()
        base.actor = "tavi"
        base.outcome = "confirmation_requested"
        base.message_id = result["message_id"]
        base.detail = {"quote_action": "request_confirmation"}
        return base

    # Already requested. Has the vendor replied since?
    last = messages.last_message(db, negotiation.id)
    if last is not None and last.sender == MessageSender.VENDOR and last.iteration > sent_at:
        # Vendor replied. Coordinator decides accept vs decline.
        result = coordinator.run_turn(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
            quote_action="respond_to_confirmation",
        )
        base.actor = "tavi"
        base.outcome = "confirmation_handled"
        base.message_id = result["message_id"]
        base.detail = {"quote_action": "respond_to_confirmation"}
        return base

    # No vendor response yet — check timeout.
    iters_waiting = iteration - sent_at
    if iters_waiting >= CONFIRMATION_TIMEOUT_TICKS:
        _force_decline(
            negotiation,
            reason=f"no response to booking confirmation within {iters_waiting} ticks",
        )
        base.actor = "system"
        base.outcome = "confirmation_timeout"
        base.detail = {"ticks_waiting": iters_waiting}
        return base

    # Still within window. Vendor's turn.
    if _vendor_skips(negotiation, vendor):
        base.actor = "vendor"
        base.outcome = "skipped"
        return base
    result = simulator.run_turn(
        db, negotiation=negotiation, work_order=work_order,
        vendor=vendor, iteration=iteration,
    )
    base.actor = "vendor"
    base.outcome = "message_sent"
    base.message_id = result["message_id"]
    return base


# ---------------------------------------------------------------------------
# Credential verification sub-phase (runs on the active pick before booking)
# ---------------------------------------------------------------------------

def _credentials_verified(work_order: WorkOrder, negotiation: Negotiation) -> bool:
    """True when every credential the work order requires has been recorded
    positively on the negotiation's attributes bag.

    If the work order doesn't require either credential, this returns True
    immediately — verification phase is skipped.
    """
    attrs = negotiation.attributes or {}
    if work_order.requires_licensed and not bool(attrs.get("license_verified")):
        return False
    if work_order.requires_insured and not bool(attrs.get("insurance_verified")):
        return False
    return True


def _run_verification(
    db: Session,
    *,
    work_order: WorkOrder,
    negotiation: Negotiation,
    vendor: Vendor,
    iteration: int,
    base: NegotiationEvent,
) -> NegotiationEvent:
    """Conduct credential verification with the active pick.

    Flow mirrors the booking-confirmation flow but with different prompts:
      - First pass: send the verification request (`quote_action=verify_credentials`).
      - Vendor reply → Tavi processes (`quote_action=process_verification`):
        may call record_facts (positive answer), send a follow-up (ambiguous),
        or decline_quote (vendor refused / can't provide).
      - Silence ≥ SILENCE_TIMEOUT_TICKS → force-decline.
      - Vendor skip / simulator invocation while within the window.
    """
    attrs = dict(negotiation.attributes or {})
    started_at = attrs.get("verification_started_at_iteration")

    if started_at is None:
        # First turn of verification. Coordinator asks.
        result = coordinator.run_turn(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
            quote_action="verify_credentials",
        )
        attrs["verification_started_at_iteration"] = iteration
        negotiation.attributes = attrs
        db.flush()
        base.actor = "tavi"
        base.outcome = "verification_requested"
        base.message_id = result["message_id"]
        base.detail = {"quote_action": "verify_credentials"}
        return base

    # In progress. Whose turn is it?
    last = messages.last_message(db, negotiation.id)

    if last is not None and last.sender == MessageSender.VENDOR:
        # Vendor replied — Tavi processes. Coordinator may record_facts,
        # send a follow-up, or decline_quote based on the reply content.
        result = coordinator.run_turn(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
            quote_action="process_verification",
        )
        base.actor = "tavi"
        base.outcome = "verification_progress"
        base.message_id = result["message_id"]
        base.detail = {"quote_action": "process_verification"}
        return base

    # Last message was Tavi — vendor's turn.
    if last is not None:
        iters_since = iteration - last.iteration
        if iters_since >= SILENCE_TIMEOUT_TICKS:
            _force_decline(
                negotiation,
                reason=f"no response during credential verification within {iters_since} ticks",
            )
            base.actor = "system"
            base.outcome = "verification_timeout"
            base.detail = {"ticks_since_last_tavi": iters_since}
            return base

    # Vendor's turn, within the window.
    if _vendor_skips(negotiation, vendor):
        base.actor = "vendor"
        base.outcome = "skipped"
        return base
    result = simulator.run_turn(
        db, negotiation=negotiation, work_order=work_order,
        vendor=vendor, iteration=iteration,
    )
    base.actor = "vendor"
    base.outcome = "message_sent"
    base.message_id = result["message_id"]
    return base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vendor_skips(negotiation: Negotiation, vendor: Vendor) -> bool:
    """Roll whether the vendor sits out this tick.

    Two cases:
      - If this vendor has been (or is now) flagged as a ghoster, they
        always skip — until the silence timeout force-declines them.
      - Otherwise, roll the persona's normal responsiveness skip probability.

    The ghoster decision is made once at the CONTACTED state's first vendor
    turn and persisted on `attributes.is_ghoster` so the behavior is stable
    across ticks (and survives backend reloads).
    """
    if _roll_or_read_ghoster(negotiation, vendor):
        return True
    return random.random() < skip_probability_for(vendor.persona_markdown)


def _roll_or_read_ghoster(negotiation: Negotiation, vendor: Vendor) -> bool:
    """Return True iff this negotiation's vendor is a ghoster. Decides on
    first call for CONTACTED-state negotiations and caches the result."""
    attrs = dict(negotiation.attributes or {})
    if "is_ghoster" in attrs:
        return bool(attrs["is_ghoster"])
    # Decide only for the initial-reply case. After CONTACTED, vendors that
    # have responded at least once aren't ghosters by definition.
    if negotiation.state != NegotiationState.CONTACTED:
        return False
    quality = max(0.0, min(1.0, vendor.cumulative_score or 0.0))
    ghost_prob = GHOST_PROB_MAX - (GHOST_PROB_MAX - GHOST_PROB_MIN) * quality
    is_ghost = random.random() < ghost_prob
    attrs["is_ghoster"] = is_ghost
    negotiation.attributes = attrs
    return is_ghost


def _roll_refusal(negotiation: Negotiation, vendor: Vendor) -> Optional[str]:
    """Return a refusal message if this vendor declines the opportunity, or
    None otherwise. Decides once on the first CONTACTED-state turn where
    the vendor would have spoken (i.e., after ghoster/persona-skip checks).

    Cached on `attributes.refused` (bool) so subsequent calls short-circuit.
    """
    attrs = dict(negotiation.attributes or {})
    if "refused" in attrs:
        # Once decided, never re-roll. `True` here doesn't mean the message
        # is still pending — it means they've already refused (and state
        # should already be DECLINED).
        return None
    if negotiation.state != NegotiationState.CONTACTED:
        return None

    quality = max(0.0, min(1.0, vendor.cumulative_score or 0.0))
    refuse_prob = REFUSE_PROB_MIN + (REFUSE_PROB_MAX - REFUSE_PROB_MIN) * quality
    if random.random() < refuse_prob:
        attrs["refused"] = True
        negotiation.attributes = attrs
        return random.choice(REFUSAL_MESSAGES)

    attrs["refused"] = False
    negotiation.attributes = attrs
    return None


def _force_decline(negotiation: Negotiation, *, reason: str) -> None:
    """Scheduler-driven termination (timeouts). Bypasses the agent tool path."""
    attrs = dict(negotiation.attributes or {})
    attrs["terminal_reason"] = reason
    negotiation.attributes = attrs
    negotiation.state = NegotiationState.DECLINED


def _active_pick_id(work_order: WorkOrder, negotiations: list[Negotiation]) -> Optional[str]:
    """Lowest-rank QUOTED neg — the one currently going through booking
    confirmation. None if no QUOTED neg with a rank exists."""
    candidates = [
        n for n in negotiations
        if n.state == NegotiationState.QUOTED and n.rank is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: n.rank or 99999)
    return candidates[0].id


def _cascade_decline_on_scheduled(negotiations: list[Negotiation]) -> None:
    """If any neg is SCHEDULED, auto-decline remaining QUOTED peers.

    Idempotent: if there's no SCHEDULED neg, no-op. If there are no QUOTED
    negs left, no-op. Call at end of each tick.
    """
    if not any(n.state == NegotiationState.SCHEDULED for n in negotiations):
        return
    for n in negotiations:
        if n.state == NegotiationState.QUOTED:
            _force_decline(n, reason="another vendor was booked")


def _refresh_quoted_ranks(
    db: Session,
    work_order: WorkOrder,
    negotiations: list[Negotiation],
    vendors_by_id: dict[str, Vendor],
) -> None:
    """Recompute subjective_rank_score + rank across QUOTED/SCHEDULED negs.

    Runs at the top of every tick so the command center can show a live
    leaderboard as vendors quote in. Uses urgency-default weights; future
    work (FM re-rank) would feed a different RankingWeights here.
    """
    weights = scoring.default_weights_for(work_order.urgency)
    relevant = [
        n for n in negotiations
        if n.state in (NegotiationState.QUOTED, NegotiationState.SCHEDULED)
        and n.quoted_price_cents is not None
    ]

    for neg in relevant:
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

    # Rank 1 = highest score. Stable sort on id for deterministic ties.
    relevant.sort(key=lambda n: (-(n.subjective_rank_score or 0.0), n.id))
    for idx, neg in enumerate(relevant, start=1):
        neg.rank = idx

    db.flush()


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
