from enum import Enum


class Trade(str, Enum):
    PLUMBING = "plumbing"
    HVAC = "hvac"
    ELECTRICAL = "electrical"
    LAWNCARE = "lawncare"
    HANDYMAN = "handyman"
    APPLIANCE_REPAIR = "appliance_repair"


class Urgency(str, Enum):
    EMERGENCY = "emergency"
    URGENT = "urgent"
    SCHEDULED = "scheduled"
    FLEXIBLE = "flexible"


class EngagementStatus(str, Enum):
    """State machine for a (work_order × vendor) engagement.

    Discovery seeds rows at PROSPECTING; subpart 3 advances them through
    the rest of the funnel.
    """

    PROSPECTING = "prospecting"
    CONTACTED = "contacted"
    QUOTED = "quoted"
    NEGOTIATING = "negotiating"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    DECLINED = "declined"
    GHOSTED = "ghosted"
