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
