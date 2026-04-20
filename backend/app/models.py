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
from .enums import EngagementStatus, Trade, Urgency


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
    """One row per (work_order × vendor). Holds the subjective per-order rank
    plus communication state + placeholders for subpart 3 to fill in.
    """

    __tablename__ = "negotiations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(String, ForeignKey("work_orders.id"), index=True)
    vendor_place_id: Mapped[str] = mapped_column(String, ForeignKey("vendors.place_id"), index=True)
    discovery_run_id: Mapped[str] = mapped_column(String, ForeignKey("discovery_runs.id"), index=True)

    subjective_rank_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subjective_rank_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    filtered: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_reasons: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    status: Mapped[EngagementStatus] = mapped_column(
        SAEnum(EngagementStatus), default=EngagementStatus.PROSPECTING
    )

    # Placeholders for subpart 3 (vendor contact / auctioning).
    messages: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, default=list, nullable=True)
    actions_log: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, default=list, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
