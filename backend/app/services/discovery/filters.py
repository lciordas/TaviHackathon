"""Hard filters applied per (work_order × vendor) before subjective ranking.

License / insurance are NOT enforced here — those are deferred to subpart 3
(vendor outreach asks the vendor directly to provide proof).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ...models import Vendor, WorkOrder
from .hours import check_overlap
from .scoring import RADIUS_MILES, haversine_miles


@dataclass
class FilterResult:
    passed: bool
    reasons: list[str]
    distance_miles: float


def apply_filters(work_order: WorkOrder, vendor: Vendor, bayes_rating_1_to_5: Optional[float]) -> FilterResult:
    reasons: list[str] = []

    if vendor.business_status and vendor.business_status != "OPERATIONAL":
        reasons.append(f"business_status_{vendor.business_status.lower()}")

    if work_order.lat is None or work_order.lng is None:
        # Caller should have geocoded already; skip distance check rather than
        # silently rejecting every vendor.
        distance = 0.0
    else:
        distance = haversine_miles(work_order.lat, work_order.lng, vendor.lat, vendor.lng)
        if distance > RADIUS_MILES:
            reasons.append(f"distance_exceeded_{round(distance, 1)}mi")

    hours = check_overlap(
        scheduled_for_utc=work_order.scheduled_for,
        regular_opening_hours=vendor.regular_opening_hours,
        utc_offset_minutes=vendor.utc_offset_minutes,
    )
    if not hours.is_open:
        reasons.append(f"hours_{hours.reason}")

    if (
        work_order.quality_threshold is not None
        and bayes_rating_1_to_5 is not None
        and bayes_rating_1_to_5 < work_order.quality_threshold
    ):
        reasons.append(
            f"below_quality_threshold_{round(bayes_rating_1_to_5, 2)}_lt_{work_order.quality_threshold}"
        )

    return FilterResult(passed=not reasons, reasons=reasons, distance_miles=distance)
