from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import EngagementStatus, Trade, Urgency


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


# ---------------------------------------------------------------------------
# Vendor discovery
# ---------------------------------------------------------------------------


class VendorRead(BaseModel):
    """Public shape for one cached vendor."""

    model_config = ConfigDict(from_attributes=True)

    place_id: str
    display_name: str
    formatted_address: Optional[str] = None
    lat: float
    lng: float
    types: list[str] = Field(default_factory=list)
    business_status: Optional[str] = None

    google_rating: Optional[float] = None
    google_user_rating_count: Optional[int] = None
    regular_opening_hours: Optional[dict[str, Any]] = None
    utc_offset_minutes: Optional[int] = None
    international_phone_number: Optional[str] = None
    website_uri: Optional[str] = None
    price_level: Optional[int] = None
    emergency_service_24_7: bool = False

    bbb_profile_url: Optional[str] = None
    bbb_grade: Optional[str] = None
    bbb_accredited: Optional[bool] = None
    bbb_years_accredited: Optional[int] = None
    bbb_complaints_total: Optional[int] = None
    bbb_complaints_resolved: Optional[int] = None
    years_in_business: Optional[int] = None

    cumulative_score: Optional[float] = None
    cumulative_score_breakdown: Optional[dict[str, Any]] = None

    google_fetched_at: datetime
    bbb_fetched_at: Optional[datetime] = None


class NegotiationRead(BaseModel):
    """Public shape for one (work_order × vendor) negotiation row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    work_order_id: str
    vendor_place_id: str
    discovery_run_id: str

    subjective_rank_score: Optional[float] = None
    subjective_rank_breakdown: Optional[dict[str, Any]] = None
    rank: Optional[int] = None

    filtered: bool
    filter_reasons: Optional[list[str]] = None

    status: EngagementStatus

    messages: Optional[list[dict[str, Any]]] = None
    actions_log: Optional[list[dict[str, Any]]] = None

    created_at: datetime
    last_updated_at: datetime


class RankedVendor(BaseModel):
    """One ranked entry in the discovery response — joins Negotiation + Vendor."""

    negotiation: NegotiationRead
    vendor: VendorRead


class DiscoveryRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    work_order_id: str
    created_at: datetime
    strategy: str
    radius_miles: int
    candidate_count: int
    cache_hit_count: int
    api_detail_calls: int
    bbb_scrape_count: int
    weight_profile: str
    duration_ms: Optional[int] = None


class DiscoveryRunRequest(BaseModel):
    work_order_id: str
    refresh: bool = False  # if True, ignore the 24h cached-run idempotency window


class DiscoveryRunResponse(BaseModel):
    run: DiscoveryRunRead
    ranked: list[RankedVendor]
    filtered: list[RankedVendor] = Field(default_factory=list)
