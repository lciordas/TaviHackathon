"""Scheduler flow tests — turn resolution, state side effects, winner-pick.

Uses the stub coordinator/simulator so no LLM is called. A real in-memory
SQLite DB (not the file) is set up fresh per test.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.enums import (
    MessageChannel,
    MessageSender,
    NegotiationState,
    Trade,
    Urgency,
)
from app.models import (
    DiscoveryRun,
    Negotiation,
    NegotiationMessage,
    Vendor,
    WorkOrder,
)
from app.services.negotiation import scheduler
from app.services.negotiation.messages import append_message


# ---------------------------------------------------------------------------
# Stub the LLM-backed coordinator + simulator for every test in this module.
# The scheduler's flow (turn resolution, skip rolls, winner-pick) is what we
# want to exercise; the Anthropic calls are covered elsewhere.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_agents(monkeypatch):
    def stub_coord(db, *, negotiation, work_order, vendor, iteration, quote_action=None):
        msg = append_message(
            db, negotiation,
            sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=iteration,
            content={"text": "stub", "subject": "stub"},
        )
        if negotiation.state == NegotiationState.QUOTED:
            if quote_action == "accept":
                negotiation.state = NegotiationState.SCHEDULED
            elif quote_action == "decline":
                negotiation.state = NegotiationState.DECLINED
            elif quote_action == "respond_to_confirmation":
                # Stub policy: assume the vendor confirmed — move to SCHEDULED.
                negotiation.state = NegotiationState.SCHEDULED
            elif quote_action == "process_verification":
                # Stub policy: treat any vendor reply as a positive
                # confirmation of all required credentials.
                attrs = dict(negotiation.attributes or {})
                attrs["license_verified"] = True
                attrs["insurance_verified"] = True
                negotiation.attributes = attrs
            # "request_confirmation" and "verify_credentials" are send-only.
        return {"message_id": msg.id, "tool_calls": []}

    def stub_sim(db, *, negotiation, work_order, vendor, iteration):
        from app.services.negotiation.messages import last_message
        last = last_message(db, negotiation.id)
        channel = last.channel if last else MessageChannel.EMAIL
        msg = append_message(
            db, negotiation,
            sender=MessageSender.VENDOR, channel=channel, iteration=iteration,
            content={"text": "stub"},
        )
        return {"message_id": msg.id}

    monkeypatch.setattr(scheduler.coordinator, "run_turn", stub_coord)
    monkeypatch.setattr(scheduler.simulator, "run_turn", stub_sim)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_work_order(db: Session, **kwargs) -> WorkOrder:
    defaults = dict(
        trade=Trade.PLUMBING,
        description="leak under sink",
        address_line="123 Main St",
        city="Dallas", state="TX", zip="75207",
        lat=32.78, lng=-96.80,
        urgency=Urgency.SCHEDULED,
        scheduled_for=datetime(2026, 4, 25, 15, 0, tzinfo=timezone.utc),
        budget_cap_cents=50000,
        quality_threshold=4.0,
        requires_licensed=True,
        requires_insured=True,
    )
    defaults.update(kwargs)
    wo = WorkOrder(**defaults)
    db.add(wo)
    db.flush()
    return wo


def _make_vendor(db: Session, place_id: str, *, name: str, cumulative: float = 0.7, responsiveness: str = "prompt") -> Vendor:
    persona = f"## Traits\n- responsiveness: {responsiveness}\n\n## Behavioral notes\n- stub"
    v = Vendor(
        place_id=place_id,
        display_name=name,
        lat=32.78, lng=-96.80,
        types=["plumber"],
        business_status="OPERATIONAL",
        cumulative_score=cumulative,
        email=f"contact@{place_id}.example",
        persona_markdown=persona,
    )
    db.add(v)
    db.flush()
    return v


def _make_run(db: Session, wo: WorkOrder) -> DiscoveryRun:
    r = DiscoveryRun(
        work_order_id=wo.id,
        strategy="searchNearby",
        radius_miles=20,
        weight_profile=wo.urgency.value,
    )
    db.add(r)
    db.flush()
    return r


def _make_neg(db: Session, wo: WorkOrder, v: Vendor, run: DiscoveryRun) -> Negotiation:
    n = Negotiation(
        work_order_id=wo.id,
        vendor_place_id=v.place_id,
        discovery_run_id=run.id,
    )
    db.add(n)
    db.flush()
    return n


def _mark_credentials_verified(neg: Negotiation) -> None:
    """Shortcut: seed the negotiation as if credential verification already
    happened (used by tests focused on the downstream booking-confirmation
    phase)."""
    attrs = dict(neg.attributes or {})
    attrs["license_verified"] = True
    attrs["insurance_verified"] = True
    neg.attributes = attrs


# ---------------------------------------------------------------------------
# Turn resolution + state side effects
# ---------------------------------------------------------------------------

def test_first_tick_on_prospecting_sends_pitch_and_transitions_to_contacted(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace", responsiveness="prompt")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)

    result = scheduler.tick(db, wo.id)
    assert result.iteration == 1
    assert len(result.events) == 1
    assert result.events[0].actor == "tavi"
    assert result.events[0].state_before == "prospecting"
    assert result.events[0].state_after == "contacted"

    db.refresh(neg)
    assert neg.state == NegotiationState.CONTACTED
    msgs = db.query(NegotiationMessage).filter(NegotiationMessage.negotiation_id == neg.id).all()
    assert len(msgs) == 1
    assert msgs[0].sender == MessageSender.TAVI
    assert msgs[0].iteration == 1


def test_contacted_to_negotiating_on_vendor_reply(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace", responsiveness="prompt")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    # Seed a prior Tavi message on iteration 1 so the thread isn't empty.
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    db.commit()

    result = scheduler.tick(db, wo.id)
    assert result.iteration == 1  # first tick on this wo
    # With prompt (10% skip), we expect the vendor to usually reply — seed RNG for determinism.


def test_vendor_skip_does_not_advance_state_but_iteration_burns(db: Session, monkeypatch):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Slowpoke", responsiveness="slow")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    db.commit()

    # Force skip.
    monkeypatch.setattr(scheduler, "_vendor_skips", lambda negotiation, vendor: True)

    result = scheduler.tick(db, wo.id)
    assert result.events[0].actor == "vendor"
    assert result.events[0].outcome == "skipped"
    assert result.events[0].state_after == "contacted"
    assert result.iteration == 1
    # No message written this tick.
    msgs = db.query(NegotiationMessage).filter(NegotiationMessage.negotiation_id == neg.id).all()
    assert len(msgs) == 0


def test_scheduled_is_skipped_waiting_for_external_signal(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.SCHEDULED
    db.commit()

    result = scheduler.tick(db, wo.id)
    assert result.events[0].outcome == "already_scheduled"
    assert result.events[0].state_after == "scheduled"


def test_terminal_negotiations_skipped(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.DECLINED
    db.commit()

    result = scheduler.tick(db, wo.id)
    assert result.events[0].outcome == "terminal"


def test_filtered_negotiations_skipped(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.filtered = True
    db.commit()

    result = scheduler.tick(db, wo.id)
    # Filtered neg should not appear in events.
    assert result.events == []
    assert result.iteration == 1


# ---------------------------------------------------------------------------
# Pre-quote phase: silence timeout + silent QUOTED peers while waiting
# ---------------------------------------------------------------------------

def test_quoted_neg_silent_while_peers_still_negotiating(db: Session):
    wo = _make_work_order(db)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 20000
    n2.state = NegotiationState.NEGOTIATING
    db.commit()

    result = scheduler.tick(db, wo.id)
    events_by_neg = {e.negotiation_id: e for e in result.events}
    # Quoted neg waits silently; negotiating peer takes its turn.
    assert events_by_neg[n1.id].outcome == "waiting"
    assert events_by_neg[n1.id].actor == "none"


def test_ghoster_flag_set_on_contacted_first_turn(db: Session, monkeypatch):
    """A low-quality vendor can be marked as a ghoster at CONTACTED — once
    flagged, they skip every tick regardless of persona responsiveness."""
    wo = _make_work_order(db)
    # cumulative=0.0 → highest ghost probability; force the die to land ghost.
    v = _make_vendor(db, "p1", name="Ghoster", cumulative=0.0, responsiveness="prompt")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    wo.loop_iteration = 1
    db.commit()

    # Force the ghost roll to succeed.
    monkeypatch.setattr("random.random", lambda: 0.0)

    # First vendor turn: ghost die rolls True → is_ghoster=True, skip.
    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "skipped"
    db.refresh(neg)
    assert neg.attributes.get("is_ghoster") is True

    # Now even with a "prompt" persona (normally 10% skip), we should always
    # skip — un-mock random to verify the cached ghoster flag wins.
    monkeypatch.undo()
    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "skipped"


def test_high_quality_vendor_rarely_ghosts(db: Session, monkeypatch):
    """cumulative=1.0 → ghost_prob clamps to GHOST_PROB_MIN; if the roll
    lands above that floor, the vendor is marked non-ghoster."""
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Premium", cumulative=1.0, responsiveness="prompt")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    wo.loop_iteration = 1
    db.commit()

    # Force the roll to land at 0.10 — above GHOST_PROB_MIN (0.05), so NOT a ghoster.
    monkeypatch.setattr("random.random", lambda: 0.10)
    scheduler.tick(db, wo.id)
    db.refresh(neg)
    assert neg.attributes.get("is_ghoster") is False


def test_high_quality_vendor_can_refuse_the_opportunity(db: Session, monkeypatch):
    """Refusal is weighted positively to quality — a 1.0-cumulative vendor
    can politely decline. Once they refuse, the neg lands in DECLINED."""
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Busy Pros", cumulative=1.0)
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    wo.loop_iteration = 1
    db.commit()

    # Sequence: ghoster roll (< 0.05 → ghost; 0.20 lands non-ghost),
    # persona skip (< 0.10 → skip; 0.20 lands non-skip), refusal roll
    # (< 0.15 → refuse; 0.05 lands refuse).
    from itertools import cycle
    rolls = cycle([0.20, 0.20, 0.05])
    monkeypatch.setattr("random.random", lambda: next(rolls))

    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "refused"
    assert r.events[0].actor == "vendor"

    db.refresh(neg)
    assert neg.state == NegotiationState.DECLINED
    assert neg.attributes.get("refused") is True
    assert neg.attributes.get("terminal_reason") == "vendor declined the opportunity"
    msgs = db.query(NegotiationMessage).filter(NegotiationMessage.negotiation_id == neg.id).all()
    # Original Tavi pitch + vendor refusal.
    assert len(msgs) == 2
    assert msgs[1].sender == MessageSender.VENDOR


def test_refusal_does_not_re_roll_on_subsequent_ticks(db: Session, monkeypatch):
    """Once `refused=False` is cached, the vendor doesn't re-roll — they
    just behave normally going forward."""
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Normal", cumulative=0.7)
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    neg.state = NegotiationState.CONTACTED
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    wo.loop_iteration = 1
    db.commit()

    # First tick: force ghoster=False, persona-skip=False, refusal=False (0.99 > any prob).
    monkeypatch.setattr("random.random", lambda: 0.99)
    scheduler.tick(db, wo.id)
    db.refresh(neg)
    assert neg.attributes.get("refused") is False
    # Now subsequent rolls of random.random() — even if they'd land in the
    # refuse window — shouldn't re-trigger a refusal since it's cached.
    # State should have transitioned to NEGOTIATING via the simulator stub.
    assert neg.state == NegotiationState.NEGOTIATING


def test_silence_timeout_terminates_non_responsive_vendor(db: Session, monkeypatch):
    """Vendor hasn't replied to a Tavi pitch for SILENCE_TIMEOUT_TICKS — auto-decline."""
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Slowpoke", responsiveness="slow")
    run = _make_run(db, wo)
    neg = _make_neg(db, wo, v, run)
    # Seed a Tavi pitch at iteration 1, leave state at CONTACTED.
    neg.state = NegotiationState.CONTACTED
    append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=MessageChannel.EMAIL, iteration=1,
        content={"text": "pitch"},
    )
    wo.loop_iteration = 1
    db.commit()

    # Force vendor to skip every tick.
    monkeypatch.setattr(scheduler, "_vendor_skips", lambda negotiation, vendor: True)

    # Iteration 2: 1 tick since pitch — within window, vendor skips.
    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "skipped"
    db.refresh(neg)
    assert neg.state == NegotiationState.CONTACTED

    # Iteration 3: 2 ticks since pitch — still within window.
    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "skipped"

    # Iteration 4: 3 ticks since pitch — SILENCE_TIMEOUT_TICKS=3 hits, force decline.
    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "silence_timeout"
    assert r.events[0].actor == "system"
    db.refresh(neg)
    assert neg.state == NegotiationState.DECLINED
    assert "no response within" in neg.attributes.get("terminal_reason", "")


# ---------------------------------------------------------------------------
# Post-ready booking confirmation flow
# ---------------------------------------------------------------------------

def test_ready_flag_triggers_confirmation_request_for_rank_1_only(db: Session):
    """With everyone QUOTED + credentials verified, rank 1 gets a confirmation request; others are queued."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    _mark_credentials_verified(n1)
    _mark_credentials_verified(n2)
    wo.ready_to_schedule = True
    db.commit()

    result = scheduler.tick(db, wo.id)
    events_by_neg = {e.negotiation_id: e for e in result.events}

    # Ace (0.9 quality) outranks Joe (0.5) under balanced SCHEDULED weights.
    db.refresh(n1); db.refresh(n2)
    assert n1.rank == 1
    assert n2.rank == 2

    # Rank 1 gets the confirmation request; rank 2 sits queued.
    assert events_by_neg[n1.id].outcome == "confirmation_requested"
    assert events_by_neg[n1.id].actor == "tavi"
    assert events_by_neg[n2.id].outcome == "queued"
    # Neither is SCHEDULED yet — waiting on vendor reply.
    assert n1.state == NegotiationState.QUOTED
    assert n2.state == NegotiationState.QUOTED
    # Confirmation marker recorded on the active pick.
    assert n1.attributes.get("booking_confirmation_requested_at_iteration") == result.iteration


def test_verification_runs_before_booking_confirmation(db: Session):
    """Rank 1 without verified credentials gets verify_credentials first,
    not request_confirmation."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    run = _make_run(db, wo)
    n = _make_neg(db, wo, v, run)
    n.state = NegotiationState.QUOTED
    n.quoted_price_cents = 25000
    wo.ready_to_schedule = True
    db.commit()

    result = scheduler.tick(db, wo.id)
    event = result.events[0]
    assert event.outcome == "verification_requested"
    assert event.actor == "tavi"
    db.refresh(n)
    # Mark set + still QUOTED (no state change).
    assert n.attributes.get("verification_started_at_iteration") is not None
    assert n.state == NegotiationState.QUOTED


def test_verification_progresses_on_vendor_reply_then_moves_to_confirmation(db: Session):
    """After the vendor replies to the verification question, Tavi processes
    it (stub records credentials), and the next tick moves to confirmation."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    run = _make_run(db, wo)
    n = _make_neg(db, wo, v, run)
    n.state = NegotiationState.QUOTED
    n.quoted_price_cents = 25000
    wo.ready_to_schedule = True
    db.commit()

    # Tick 1: verification_requested sent.
    r1 = scheduler.tick(db, wo.id)
    assert r1.events[0].outcome == "verification_requested"

    # Simulate a vendor reply ("yes, we're licensed and insured").
    db.refresh(n)
    append_message(
        db, n,
        sender=MessageSender.VENDOR, channel=MessageChannel.EMAIL,
        iteration=r1.iteration + 1,
        content={"text": "yes, licensed TX-1234 and insured through Acme Insurance"},
    )
    db.commit()

    # Tick 2: Tavi processes verification → stub records creds verified.
    r2 = scheduler.tick(db, wo.id)
    assert r2.events[0].outcome == "verification_progress"
    db.refresh(n)
    assert n.attributes.get("license_verified") is True
    assert n.attributes.get("insurance_verified") is True

    # Tick 3: credentials verified → booking confirmation request.
    r3 = scheduler.tick(db, wo.id)
    assert r3.events[0].outcome == "confirmation_requested"


def test_verification_silence_timeout_moves_to_next_rank(db: Session, monkeypatch):
    """Vendor silent through the verification window → force-decline rank 1,
    rank 2 becomes the pick and enters verification next."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    wo.ready_to_schedule = True
    db.commit()

    # Vendor always skips the reply.
    monkeypatch.setattr(scheduler, "_vendor_skips", lambda negotiation, vendor: True)

    # Tick 1: rank 1 gets verify_credentials.
    r1 = scheduler.tick(db, wo.id)
    events = {e.negotiation_id: e for e in r1.events}
    assert events[n1.id].outcome == "verification_requested"

    # Ticks 2 + 3: rank 1 vendor skips. Tick 4: silence timeout fires
    # (3 iterations since the Tavi verification message).
    scheduler.tick(db, wo.id)  # tick 2
    scheduler.tick(db, wo.id)  # tick 3
    r4 = scheduler.tick(db, wo.id)
    events = {e.negotiation_id: e for e in r4.events}
    assert events[n1.id].outcome == "verification_timeout"
    db.refresh(n1)
    assert n1.state == NegotiationState.DECLINED

    # Tick 5: rank 2 is the new active pick; verification starts for it.
    r5 = scheduler.tick(db, wo.id)
    events = {e.negotiation_id: e for e in r5.events}
    assert events[n2.id].outcome == "verification_requested"


def test_credentials_already_on_file_skip_verification(db: Session):
    """If the work order's required creds are already recorded, verification
    is skipped and we go straight to booking confirmation."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    run = _make_run(db, wo)
    n = _make_neg(db, wo, v, run)
    n.state = NegotiationState.QUOTED
    n.quoted_price_cents = 25000
    _mark_credentials_verified(n)
    wo.ready_to_schedule = True
    db.commit()

    r = scheduler.tick(db, wo.id)
    assert r.events[0].outcome == "confirmation_requested"


def test_confirmation_timeout_moves_to_next_rank(db: Session, monkeypatch):
    """If rank 1 doesn't reply within CONFIRMATION_TIMEOUT_TICKS, force-decline and rank 2 becomes the pick."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    _mark_credentials_verified(n1)
    _mark_credentials_verified(n2)
    wo.ready_to_schedule = True
    db.commit()

    # Force vendor to always skip.
    monkeypatch.setattr(scheduler, "_vendor_skips", lambda negotiation, vendor: True)

    # Tick 1: Ace gets confirmation request.
    r1 = scheduler.tick(db, wo.id)
    assert {e.negotiation_id: e.outcome for e in r1.events}[n1.id] == "confirmation_requested"

    # Tick 2: Ace's vendor skips (1 tick of silence since request).
    r2 = scheduler.tick(db, wo.id)
    assert {e.negotiation_id: e.outcome for e in r2.events}[n1.id] == "skipped"

    # Tick 3: 2 ticks of silence since request — CONFIRMATION_TIMEOUT_TICKS hits.
    r3 = scheduler.tick(db, wo.id)
    events3 = {e.negotiation_id: e.outcome for e in r3.events}
    assert events3[n1.id] == "confirmation_timeout"
    db.refresh(n1)
    assert n1.state == NegotiationState.DECLINED
    assert "no response to booking confirmation" in n1.attributes.get("terminal_reason", "")

    # Tick 4: Joe is now the active pick and gets a confirmation request.
    r4 = scheduler.tick(db, wo.id)
    events4 = {e.negotiation_id: e.outcome for e in r4.events}
    assert events4[n2.id] == "confirmation_requested"


def test_vendor_confirms_triggers_accept_and_cascade_decline(db: Session):
    """Vendor reply to confirmation → coordinator accepts → other QUOTED peers decline."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    _mark_credentials_verified(n1)
    _mark_credentials_verified(n2)
    wo.ready_to_schedule = True
    db.commit()

    # Tick 1: confirmation request goes to Ace.
    scheduler.tick(db, wo.id)
    db.refresh(n1)
    sent_iter = n1.attributes["booking_confirmation_requested_at_iteration"]

    # Seed a vendor reply on a later iteration — as if the simulator had replied.
    append_message(
        db, n1,
        sender=MessageSender.VENDOR, channel=MessageChannel.EMAIL,
        iteration=sent_iter + 1,
        content={"text": "yes, confirmed, see you then"},
    )
    db.commit()

    # Tick 2: scheduler sees vendor reply → respond_to_confirmation →
    # stub coordinator calls "accept" → Ace → SCHEDULED, Joe auto-declined.
    result = scheduler.tick(db, wo.id)
    db.refresh(n1); db.refresh(n2)
    assert n1.state == NegotiationState.SCHEDULED
    assert n2.state == NegotiationState.DECLINED
    assert n2.attributes.get("terminal_reason") == "another vendor was booked"


def test_subjective_rank_refreshes_on_partial_quotes(db: Session):
    """As quotes come in, ranks should update live — not wait for the ready flag."""
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    v3 = _make_vendor(db, "p3", name="Pam", cumulative=0.7)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n3 = _make_neg(db, wo, v3, run)

    # Two have quoted so far; Pam is still NEGOTIATING. ready_to_schedule
    # stays false, but the two that have quoted should get live ranks.
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    n3.state = NegotiationState.NEGOTIATING
    db.commit()

    scheduler.tick(db, wo.id)

    db.refresh(n1); db.refresh(n2); db.refresh(n3); db.refresh(wo)
    assert wo.ready_to_schedule is False  # Pam still negotiating
    # Both quoted negs have a subjective score + rank set.
    assert n1.subjective_rank_score is not None
    assert n2.subjective_rank_score is not None
    assert {n1.rank, n2.rank} == {1, 2}
    # Ace wins at SCHEDULED urgency (0.9 quality beats 0.5 even at higher price).
    assert n1.rank == 1
    # Still-negotiating neg stays unranked.
    assert n3.rank is None


def test_ready_to_schedule_stays_false_while_anyone_negotiating(db: Session):
    wo = _make_work_order(db)
    v1 = _make_vendor(db, "p1", name="Ace")
    v2 = _make_vendor(db, "p2", name="Joe")
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.NEGOTIATING
    db.commit()

    scheduler.tick(db, wo.id)
    db.refresh(wo)
    assert wo.ready_to_schedule is False


def test_ready_to_schedule_flips_true_when_all_quoted_or_terminal(db: Session):
    wo = _make_work_order(db)
    v1 = _make_vendor(db, "p1", name="Ace")
    v2 = _make_vendor(db, "p2", name="Joe")
    v3 = _make_vendor(db, "p3", name="Pam")
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    n3 = _make_neg(db, wo, v3, run)
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 30000
    n3.state = NegotiationState.DECLINED
    db.commit()

    scheduler.tick(db, wo.id)
    db.refresh(wo)
    assert wo.ready_to_schedule is True


def test_ready_to_schedule_ignores_filtered_negotiations(db: Session):
    wo = _make_work_order(db)
    v1 = _make_vendor(db, "p1", name="Ace")
    v2 = _make_vendor(db, "p2", name="Joe")
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    # n1 is the only "real" candidate; n2 was excluded at discovery.
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.filtered = True
    n2.state = NegotiationState.PROSPECTING
    db.commit()

    scheduler.tick(db, wo.id)
    db.refresh(wo)
    # Filtered neg's PROSPECTING state doesn't block the flag.
    assert wo.ready_to_schedule is True


def test_ready_to_schedule_is_monotonic(db: Session):
    """Once True, the flag never flips back to False even if state drifts."""
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    n = _make_neg(db, wo, v, run)
    n.state = NegotiationState.QUOTED
    n.quoted_price_cents = 25000
    db.commit()

    scheduler.tick(db, wo.id)
    db.refresh(wo)
    assert wo.ready_to_schedule is True

    # Artificially revert state (shouldn't happen in practice, but guards
    # against defensive code flipping the flag off).
    n.state = NegotiationState.NEGOTIATING
    db.commit()

    scheduler.tick(db, wo.id)
    db.refresh(wo)
    assert wo.ready_to_schedule is True


def test_iteration_increments_each_tick(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    _make_neg(db, wo, v, run)

    for expected in range(1, 4):
        result = scheduler.tick(db, wo.id)
        assert result.iteration == expected
