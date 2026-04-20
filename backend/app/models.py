from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .enums import Trade, Urgency


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
