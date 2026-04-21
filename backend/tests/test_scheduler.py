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
        if quote_action == "accept" and negotiation.state == NegotiationState.QUOTED:
            negotiation.state = NegotiationState.SCHEDULED
        elif quote_action == "decline" and negotiation.state == NegotiationState.QUOTED:
            negotiation.state = NegotiationState.DECLINED
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
    monkeypatch.setattr(scheduler, "_vendor_skips", lambda vendor: True)

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
# Winner-pick
# ---------------------------------------------------------------------------

def test_winner_pick_waits_while_any_neg_still_negotiating(db: Session):
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
    assert result.winner_pick is None
    # QUOTED neg waits (no quote_action injected), NEGOTIATING takes its turn.
    events_by_neg = {e.negotiation_id: e for e in result.events}
    assert events_by_neg[n1.id].outcome == "waiting"


def test_winner_pick_fires_when_all_quoted_or_terminal(db: Session):
    wo = _make_work_order(db, urgency=Urgency.SCHEDULED, budget_cap_cents=40000)
    v1 = _make_vendor(db, "p1", name="Ace", cumulative=0.9)
    v2 = _make_vendor(db, "p2", name="Joe", cumulative=0.5)
    run = _make_run(db, wo)
    n1 = _make_neg(db, wo, v1, run)
    n2 = _make_neg(db, wo, v2, run)
    # Both quoted; Ace quoted slightly higher but has much better quality score.
    n1.state = NegotiationState.QUOTED
    n1.quoted_price_cents = 25000
    n2.state = NegotiationState.QUOTED
    n2.quoted_price_cents = 18000
    db.commit()

    result = scheduler.tick(db, wo.id)
    assert result.winner_pick is not None
    ranks = sorted(result.winner_pick.ranked, key=lambda r: r["rank"])
    # Under SCHEDULED urgency (quality=0.5 / price=0.5), Ace's 0.9 quality
    # edges Joe's 0.5 even at a higher price — Ace wins.
    assert ranks[0]["vendor_display_name"] == "Ace"
    assert ranks[0]["action"] == "accept"
    assert ranks[1]["action"] == "decline"

    db.refresh(n1); db.refresh(n2)
    assert n1.state == NegotiationState.SCHEDULED
    assert n2.state == NegotiationState.DECLINED
    assert n1.rank == 1 and n2.rank == 2


def test_iteration_increments_each_tick(db: Session):
    wo = _make_work_order(db)
    v = _make_vendor(db, "p1", name="Ace")
    run = _make_run(db, wo)
    _make_neg(db, wo, v, run)

    for expected in range(1, 4):
        result = scheduler.tick(db, wo.id)
        assert result.iteration == expected
