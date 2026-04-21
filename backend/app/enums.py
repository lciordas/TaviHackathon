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


class NegotiationState(str, Enum):
    """State machine for a (work_order × vendor) negotiation.

    The first five are active; the last four are terminal (write-once).
    See `docs/Step 3.md` for the authoritative transition table.
    """

    # Active
    PROSPECTING = "prospecting"
    CONTACTED = "contacted"
    NEGOTIATING = "negotiating"
    QUOTED = "quoted"
    SCHEDULED = "scheduled"
    # Terminal
    COMPLETED = "completed"
    NOSHOW = "noshow"
    DECLINED = "declined"
    CANCELLED = "cancelled"


ACTIVE_STATES: frozenset[NegotiationState] = frozenset(
    {
        NegotiationState.PROSPECTING,
        NegotiationState.CONTACTED,
        NegotiationState.NEGOTIATING,
        NegotiationState.QUOTED,
        NegotiationState.SCHEDULED,
    }
)
TERMINAL_STATES: frozenset[NegotiationState] = frozenset(
    {
        NegotiationState.COMPLETED,
        NegotiationState.NOSHOW,
        NegotiationState.DECLINED,
        NegotiationState.CANCELLED,
    }
)


class MessageSender(str, Enum):
    TAVI = "tavi"
    VENDOR = "vendor"


class MessageChannel(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    PHONE = "phone"
