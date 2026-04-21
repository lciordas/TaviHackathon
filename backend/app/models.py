from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .enums import MessageChannel, MessageSender, NegotiationState, Trade, Urgency


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    created_by: Mapped[str] = mapped_column(String, default="default_user")

    trade: Mapped[Trade] = mapped_column(SAEnum(Trade), index=True)
    description: Mapped[str] = mapped_column(Text)

    # Address comes from the frontend's Google Places autocomplete widget,
    # not from the chat LLM. Required: vendor discovery needs lat/lng to
    # compute distances.
    address_line: Mapped[str] = mapped_column(String)
    city: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String(2))
    zip: Mapped[str] = mapped_column(String)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    access_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    urgency: Mapped[Urgency] = mapped_column(SAEnum(Urgency))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime)

    budget_cap_cents: Mapped[int] = mapped_column(Integer)

    quality_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    requires_licensed: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_insured: Mapped[bool] = mapped_column(Boolean, default=True)

    # Iteration counter for the negotiation top-loop. Time-in-the-demo is
    # measured in ticks, not wall-clock seconds; each scheduler pass for this
    # work order increments this by one. `negotiation_messages.iteration`
    # records which tick a message was written on, so the UI can visualize
    # vendor latency as a gap between iterations.
    loop_iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Gate that marks the work order as ready for a vendor to be booked.
    # Starts false at intake; flips true when downstream logic decides the
    # quote pool is mature enough to lock in a winner.
    ready_to_schedule: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Pitch template cache — one LLM call per work order, reused across
    # every vendor's PROSPECTING turn. Stored as JSON {subject, body}; the
    # body uses {{vendor_name}} as the per-vendor fill.
    pitch_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Vendor(Base):
    """Cache + objective scoreline for one business. Keyed by Google place_id.

    Built from two real sources: Google Places API (new) and BBB scrape.
    `cumulative_score` is the order-independent quality score used as one
    input to the per-order subjective ranking on Negotiation rows.
    """

    __tablename__ = "vendors"

    place_id: Mapped[str] = mapped_column(String, primary_key=True)

    # --- Google Places fields ---
    display_name: Mapped[str] = mapped_column(String)
    formatted_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    types: Mapped[list[str]] = mapped_column(JSON, default=list)
    business_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    google_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    google_user_rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    regular_opening_hours: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    utc_offset_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    international_phone_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    website_uri: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    emergency_service_24_7: Mapped[bool] = mapped_column(Boolean, default=False)

    # Google Places doesn't expose contact email. We synthesize one at
    # persona-assign time (`contact@{slug}.example`) so the channel-selection
    # rule (email → sms → phone) always exercises the email-first path in the
    # demo. Real integrations would replace this with a verified address.
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Persona trait pack (markdown) used by the vendor simulator. Assigned
    # randomly from a fixed pool on first cache, then stable across
    # re-discoveries. See `backend/app/personas/pool/`.
    persona_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- BBB fields (nullable — vendor may not have a BBB profile) ---
    bbb_profile_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bbb_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bbb_accredited: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    bbb_years_accredited: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bbb_complaints_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bbb_complaints_resolved: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    years_in_business: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Computed objective score ---
    cumulative_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cumulative_score_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # --- Provenance ---
    google_fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    bbb_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DiscoveryRun(Base):
    """One row per `/discovery/run` invocation. Audit + cost tracking."""

    __tablename__ = "discovery_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(String, ForeignKey("work_orders.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    strategy: Mapped[str] = mapped_column(String)  # "searchNearby" | "searchText"
    radius_miles: Mapped[int] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    api_detail_calls: Mapped[int] = mapped_column(Integer, default=0)
    bbb_scrape_count: Mapped[int] = mapped_column(Integer, default=0)
    weight_profile: Mapped[str] = mapped_column(String)  # urgency name
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Negotiation(Base):
    """One row per (work_order × vendor).

    Holds the negotiation state machine, the vendor's firm quote (if one
    arrives), the subpart-2 ranking output, and a freeform `attributes` JSON
    for whatever the coordinator extracts during the conversation (insurance,
    license, scope notes, escalation reason, terminal reason).

    The message thread lives in `negotiation_messages`, keyed by
    `negotiation_id`.
    """

    __tablename__ = "negotiations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(String, ForeignKey("work_orders.id"), index=True)
    vendor_place_id: Mapped[str] = mapped_column(String, ForeignKey("vendors.place_id"), index=True)
    discovery_run_id: Mapped[str] = mapped_column(String, ForeignKey("discovery_runs.id"), index=True)

    # --- Subpart 2 ranking (set at discovery time, refined after quotes arrive) ---
    # Subjective per-order rank. Populated by `scoring.compute_subjective` once
    # a quote is recorded; the winner-pick step sorts by this.
    subjective_rank_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subjective_rank_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    filtered: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_reasons: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    # --- Subpart 3 state machine + quote ---
    state: Mapped[NegotiationState] = mapped_column(
        SAEnum(NegotiationState), default=NegotiationState.PROSPECTING, index=True
    )

    # Firm-terms pair. Both null until state = QUOTED.
    quoted_price_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quoted_available_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Freeform bag for extracted facts (insurance_verified, license_number,
    # availability notes), escalation_reason, terminal_reason — anything the
    # coordinator records via `record_facts` / `close_negotiation` /
    # `escalate`. Structure is intentionally loose.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class NegotiationMessage(Base):
    """One message in a negotiation thread.

    Every outbound action from the Tavi Coordinator (send_email / send_sms /
    send_phone) and every reply from the vendor simulator lands here. Even
    when real SMTP / SMS gateways are added later, the DB row remains the
    canonical conversation history — delivery is a side effect layered on top.

    `iteration` records the scheduler tick on which this message was authored.
    Paired with `WorkOrder.loop_iteration`, the UI can compute and render
    per-message latency ("vendor went cold for 3 ticks").
    """

    __tablename__ = "negotiation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    negotiation_id: Mapped[str] = mapped_column(
        String, ForeignKey("negotiations.id"), index=True
    )
    sender: Mapped[MessageSender] = mapped_column(SAEnum(MessageSender))
    channel: Mapped[MessageChannel] = mapped_column(SAEnum(MessageChannel))
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
