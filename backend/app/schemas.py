from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import Trade, Urgency


# Fields that must be non-None before a work order can be persisted.
# Address + lat/lng are populated by the frontend's Google Places autocomplete
# widget (not by the LLM), but they're still required for a valid order.
REQUIRED_FIELDS: tuple[str, ...] = (
    "trade",
    "description",
    "address_line",
    "city",
    "state",
    "zip",
    "lat",
    "lng",
    "urgency",
    "scheduled_for",
    "budget_cap_cents",
    "quality_threshold",
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
    # Transient: the LLM puts a free-text address it extracted from chat here
    # so the UI's autocomplete input can seed from it. NOT persisted to DB —
    # the frontend flow overwrites with the user-picked structured address.
    address_hint: Optional[str] = None
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
    address_line: str
    city: str
    state: str
    zip: str
    lat: float
    lng: float
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


# ---------------------------------------------------------------------------
# Address autocomplete (Places API proxy — keeps API key server-side)
# ---------------------------------------------------------------------------


class PlacesAutocompleteRequest(BaseModel):
    query: str = Field(min_length=1)
    lat: Optional[float] = None  # optional bias toward user's rough area
    lng: Optional[float] = None


class PlacesAutocompleteSuggestion(BaseModel):
    place_id: str
    primary_text: str  # e.g., "2304 Stemmons Trail"
    secondary_text: str  # e.g., "Dallas, TX 75207, USA"


class PlacesAutocompleteResponse(BaseModel):
    suggestions: list[PlacesAutocompleteSuggestion]


class PlacesSelectRequest(BaseModel):
    place_id: str


class PlacesSelectResponse(BaseModel):
    """Structured address + lat/lng for one selected suggestion."""

    address_line: str
    city: str
    state: str
    zip: str
    lat: float
    lng: float
    formatted_address: str
