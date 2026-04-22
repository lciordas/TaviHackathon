"""Coordinator tool executors — state transitions + attributes merge + guards.

Pure-DB tests. No Anthropic calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.enums import MessageChannel, MessageSender, NegotiationState, Trade, Urgency
from app.models import DiscoveryRun, Negotiation, NegotiationMessage, Vendor, WorkOrder
from app.services.negotiation import tools


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(bind=engine)
    s = S()
    try:
        yield s
    finally:
        s.close()


def _seed(db: Session, state: NegotiationState = NegotiationState.PROSPECTING) -> Negotiation:
    wo = WorkOrder(
        trade=Trade.PLUMBING, description="leak",
        address_line="1 A St", city="Dallas", state="TX", zip="75207",
        lat=32.78, lng=-96.80, urgency=Urgency.SCHEDULED,
        scheduled_for=datetime(2026, 4, 25, tzinfo=timezone.utc),
        budget_cap_cents=50000, quality_threshold=4.0,
        email="fm@example.com",
    )
    db.add(wo); db.flush()
    v = Vendor(place_id="p1", display_name="Ace", lat=32.78, lng=-96.80, types=[])
    db.add(v); db.flush()
    run = DiscoveryRun(work_order_id=wo.id, strategy="searchNearby", radius_miles=20, weight_profile="scheduled")
    db.add(run); db.flush()
    neg = Negotiation(work_order_id=wo.id, vendor_place_id=v.place_id, discovery_run_id=run.id, state=state)
    db.add(neg); db.flush()
    return neg


def test_send_email_appends_message_and_transitions_prospecting_to_contacted(db: Session):
    neg = _seed(db, NegotiationState.PROSPECTING)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="send_email",
                         tool_input={"subject": "Hi", "body": "Reaching out about a plumbing job."})
    assert out.success
    assert neg.state == NegotiationState.CONTACTED
    msgs = db.query(NegotiationMessage).all()
    assert len(msgs) == 1
    assert msgs[0].sender == MessageSender.TAVI
    assert msgs[0].channel == MessageChannel.EMAIL
    assert msgs[0].content["subject"] == "Hi"


def test_send_sms_uses_sms_channel_and_transitions_state(db: Session):
    neg = _seed(db, NegotiationState.PROSPECTING)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="send_sms",
                         tool_input={"text": "Hey — got a plumbing job, interested?"})
    assert out.success
    assert neg.state == NegotiationState.CONTACTED
    m = db.query(NegotiationMessage).one()
    assert m.channel == MessageChannel.SMS


def test_send_rejects_empty_body(db: Session):
    neg = _seed(db, NegotiationState.PROSPECTING)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="send_email",
                         tool_input={"subject": "x", "body": "   "})
    assert not out.success
    assert "empty" in out.message
    assert db.query(NegotiationMessage).count() == 0


def test_record_quote_requires_negotiating(db: Session):
    neg = _seed(db, NegotiationState.CONTACTED)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="record_quote",
                         tool_input={"price_cents": 25000, "available_at": "2026-04-25T10:00:00Z"})
    assert not out.success
    assert "NEGOTIATING" in out.message


def test_record_quote_sets_fields_and_transitions(db: Session):
    neg = _seed(db, NegotiationState.NEGOTIATING)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="record_quote",
                         tool_input={"price_cents": 25000, "available_at": "2026-04-25T10:00:00Z"})
    assert out.success
    assert neg.state == NegotiationState.QUOTED
    assert neg.quoted_price_cents == 25000
    assert neg.quoted_available_at.isoformat().startswith("2026-04-25T10:00:00")


def test_record_facts_merges_into_attributes(db: Session):
    neg = _seed(db, NegotiationState.NEGOTIATING)
    neg.attributes = {"existing_key": "preserved"}
    db.flush()

    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="record_facts",
                         tool_input={"facts": {"insurance_verified": True, "license_number": "TX-12345"}})
    assert out.success
    assert neg.attributes == {
        "existing_key": "preserved",
        "insurance_verified": True,
        "license_number": "TX-12345",
    }


def test_accept_quote_requires_quoted(db: Session):
    neg = _seed(db, NegotiationState.NEGOTIATING)
    out = tools.dispatch(db, negotiation=neg, iteration=1, tool_name="accept_quote", tool_input={})
    assert not out.success
    assert "QUOTED" in out.message


def test_accept_quote_transitions_to_scheduled(db: Session):
    neg = _seed(db, NegotiationState.QUOTED)
    out = tools.dispatch(db, negotiation=neg, iteration=1, tool_name="accept_quote", tool_input={})
    assert out.success
    assert neg.state == NegotiationState.SCHEDULED


def test_decline_quote_sets_terminal_reason(db: Session):
    neg = _seed(db, NegotiationState.QUOTED)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="decline_quote",
                         tool_input={"reason": "went with another vendor"})
    assert out.success
    assert neg.state == NegotiationState.DECLINED
    assert neg.attributes["terminal_reason"] == "went with another vendor"


def test_close_negotiation_only_from_active_pre_quote(db: Session):
    neg = _seed(db, NegotiationState.QUOTED)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="close_negotiation",
                         tool_input={"reason": "out of scope"})
    assert not out.success


def test_close_negotiation_from_negotiating(db: Session):
    neg = _seed(db, NegotiationState.NEGOTIATING)
    out = tools.dispatch(db, negotiation=neg, iteration=1,
                         tool_name="close_negotiation",
                         tool_input={"reason": "out of scope"})
    assert out.success
    assert neg.state == NegotiationState.DECLINED
    assert neg.attributes["terminal_reason"] == "out of scope"


def test_unknown_tool_returns_error(db: Session):
    neg = _seed(db, NegotiationState.PROSPECTING)
    out = tools.dispatch(db, negotiation=neg, iteration=1, tool_name="do_magic", tool_input={})
    assert not out.success
    assert "unknown" in out.message
