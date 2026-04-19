from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import Trade, Urgency


# Fields that must be non-None before a work order can be persisted.
# Address / lat / lng deliberately excluded — those belong to the deferred
# Google Places integration.
REQUIRED_FIELDS: tuple[str, ...] = (
    "trade",
    "description",
    "urgency",
    "scheduled_for",
    "budget_cap_cents",
    "requires_licensed",
    "requires_insured",
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class WorkOrderPartial(BaseModel):
    """Per-turn mutable state. All fields optional until finalize."""

    model_config = ConfigDict(extra="ignore")

    trade: Optional[Trade] = None
    description: Optional[str] = None
    address_line: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    access_notes: Optional[str] = None
    urgency: Optional[Urgency] = None
    scheduled_for: Optional[datetime] = None
    budget_cap_cents: Optional[int] = None
    quality_threshold: Optional[float] = None
    requires_licensed: Optional[bool] = None
    requires_insured: Optional[bool] = None

    def merge(self, patch: "WorkOrderPartial") -> "WorkOrderPartial":
        data = self.model_dump()
        for key, value in patch.model_dump(exclude_unset=True).items():
            if value is not None:
                data[key] = value
        return WorkOrderPartial(**data)


class IntakeStartResponse(BaseModel):
    greeting: str
    fields: WorkOrderPartial


class IntakeTurnRequest(BaseModel):
    messages: list[ChatMessage]
    fields: WorkOrderPartial = Field(default_factory=WorkOrderPartial)


class IntakeTurnResponse(BaseModel):
    reply: str
    fields: WorkOrderPartial
    is_ready: bool
    missing: list[str]


class IntakeConfirmRequest(BaseModel):
    fields: WorkOrderPartial


class WorkOrderRead(BaseModel):
    """Shape returned after a WorkOrder is persisted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    created_by: str
    trade: Trade
    description: str
    address_line: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    access_notes: Optional[str] = None
    urgency: Urgency
    scheduled_for: datetime
    budget_cap_cents: int
    quality_threshold: Optional[float] = None
    requires_licensed: bool
    requires_insured: bool


class IntakeConfirmResponse(BaseModel):
    id: str
    work_order: WorkOrderRead
