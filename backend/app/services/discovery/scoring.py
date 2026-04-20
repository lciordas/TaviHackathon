"""Scoring formulas for vendor discovery.

Two distinct scores:

- `cumulative_score` (objective, on Vendor row): stable per vendor across all
  customers. Combines Bayesian-adjusted Google rating, BBB grade, BBB complaint
  resolution rate, and tenure-from-BBB.

- `subjective_rank_score` (per-order, on Negotiation row): blends the cumulative
  score with order-specific fit signals (distance, 24/7 availability, budget
  fit). Weights shift based on `WorkOrder.urgency`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ...enums import Urgency


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BAYES_PRIOR_STRENGTH = 50
BAYES_PRIOR_RATING = 4.0
RADIUS_MILES = 20.0

BBB_GRADE_MAP: dict[str, float] = {
    "A+": 1.00, "A": 0.90, "A-": 0.85,
    "B+": 0.75, "B": 0.65, "B-": 0.55,
    "C+": 0.40, "C": 0.30, "C-": 0.20,
    "D": 0.10, "F": 0.00,
    "NR": 0.50,  # Not Rated — neutral
}

CUMULATIVE_WEIGHTS = {
    "bayes_rating": 0.45,
    "bbb_grade": 0.25,
    "resolution_rate": 0.10,
    "tenure": 0.20,
}

# Per-urgency weights for the subjective per-order score. Each column sums to 1.0.
SUBJECTIVE_WEIGHTS: dict[Urgency, dict[str, float]] = {
    Urgency.EMERGENCY: {"cumulative": 0.30, "distance_fit": 0.45, "h24_7_fit": 0.25, "budget_fit": 0.00},
    Urgency.URGENT:    {"cumulative": 0.45, "distance_fit": 0.30, "h24_7_fit": 0.10, "budget_fit": 0.15},
    Urgency.SCHEDULED: {"cumulative": 0.60, "distance_fit": 0.15, "h24_7_fit": 0.00, "budget_fit": 0.25},
    Urgency.FLEXIBLE:  {"cumulative": 0.55, "distance_fit": 0.15, "h24_7_fit": 0.00, "budget_fit": 0.30},
}


# ---------------------------------------------------------------------------
# Cumulative (objective) score
# ---------------------------------------------------------------------------

@dataclass
class CumulativeResult:
    score: float                                    # in [0, 1]
    bayes_rating_1_to_5: Optional[float]            # for quality_threshold filter
    breakdown: dict


def bayes_rating(rating: Optional[float], count: Optional[int]) -> Optional[float]:
    """Bayesian-adjusted average. Returns None if rating is missing.

    `(v / (v + m)) * R + (m / (v + m)) * C` — anchors low-review-count vendors
    toward the global prior so a 5.0 with 3 reviews doesn't outrank a 4.4 with 800.
    """
    if rating is None:
        return None
    v = max(0, count or 0)
    m = BAYES_PRIOR_STRENGTH
    return (v / (v + m)) * rating + (m / (v + m)) * BAYES_PRIOR_RATING


def _bbb_grade_score(grade: Optional[str]) -> Optional[float]:
    if grade is None:
        return None
    return BBB_GRADE_MAP.get(grade.strip(), 0.5)


def _resolution_rate(total: Optional[int], resolved: Optional[int]) -> Optional[float]:
    if total is None:
        return None
    if total == 0:
        return 1.0
    if resolved is None:
        return 0.5
    return max(0.0, min(1.0, resolved / total))


def _tenure_score(years: Optional[int]) -> Optional[float]:
    if years is None:
        return None
    return min(years / 15.0, 1.0)


def compute_cumulative(
    *,
    google_rating: Optional[float],
    google_user_rating_count: Optional[int],
    bbb_grade: Optional[str],
    bbb_complaints_total: Optional[int],
    bbb_complaints_resolved: Optional[int],
    years_in_business: Optional[int],
) -> CumulativeResult:
    """Combine objective signals into a single [0,1] score.

    Missing signals are dropped from the weighted average and the remaining
    weights renormalize. So a vendor with no BBB profile is scored on the
    Bayesian rating alone (with bayes_rating taking 100% of the weight).
    """
    bayes_1_5 = bayes_rating(google_rating, google_user_rating_count)
    bayes_norm = (bayes_1_5 - 1) / 4 if bayes_1_5 is not None else None
    bbb_score = _bbb_grade_score(bbb_grade)
    res_rate = _resolution_rate(bbb_complaints_total, bbb_complaints_resolved)
    tenure = _tenure_score(years_in_business)

    signals = {
        "bayes_rating": bayes_norm,
        "bbb_grade": bbb_score,
        "resolution_rate": res_rate,
        "tenure": tenure,
    }
    available = {k: v for k, v in signals.items() if v is not None}
    if not available:
        return CumulativeResult(score=0.0, bayes_rating_1_to_5=None,
                                breakdown={"signals": signals, "weights_applied": {}})

    weight_sum = sum(CUMULATIVE_WEIGHTS[k] for k in available)
    weights_applied = {k: CUMULATIVE_WEIGHTS[k] / weight_sum for k in available}
    score = sum(available[k] * weights_applied[k] for k in available)
    score = max(0.0, min(1.0, score))

    return CumulativeResult(
        score=round(score, 4),
        bayes_rating_1_to_5=round(bayes_1_5, 3) if bayes_1_5 is not None else None,
        breakdown={"signals": {k: (round(v, 3) if v is not None else None) for k, v in signals.items()},
                   "weights_applied": {k: round(w, 3) for k, w in weights_applied.items()}},
    )


# ---------------------------------------------------------------------------
# Subjective (per-order) score
# ---------------------------------------------------------------------------

@dataclass
class SubjectiveResult:
    score: float                                    # in [0, 1]
    breakdown: dict


def distance_fit(distance_miles: float) -> float:
    return max(0.0, 1.0 - distance_miles / RADIUS_MILES)


def budget_fit(price_level: Optional[int]) -> float:
    """Map Google price_level [0..4] to fit score. Cheaper = better fit for
    budget-capped commercial jobs. Unknown → neutral 0.5."""
    if price_level is None:
        return 0.5
    return {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.3}.get(price_level, 0.5)


def compute_subjective(
    *,
    cumulative_score: float,
    urgency: Urgency,
    distance_miles: float,
    emergency_service_24_7: bool,
    price_level: Optional[int],
) -> SubjectiveResult:
    weights = SUBJECTIVE_WEIGHTS[urgency]
    d_fit = distance_fit(distance_miles)
    h_fit = 1.0 if emergency_service_24_7 and urgency in (Urgency.EMERGENCY, Urgency.URGENT) else 0.0
    b_fit = budget_fit(price_level)

    contributions = {
        "cumulative": cumulative_score * weights["cumulative"],
        "distance_fit": d_fit * weights["distance_fit"],
        "h24_7_fit": h_fit * weights["h24_7_fit"],
        "budget_fit": b_fit * weights["budget_fit"],
    }
    score = sum(contributions.values())
    score = max(0.0, min(1.0, score))

    return SubjectiveResult(
        score=round(score, 4),
        breakdown={
            "weights_profile": urgency.value,
            "weights": weights,
            "signals": {
                "cumulative": round(cumulative_score, 3),
                "distance_fit": round(d_fit, 3),
                "h24_7_fit": h_fit,
                "budget_fit": round(b_fit, 3),
            },
            "contributions": {k: round(v, 4) for k, v in contributions.items()},
        },
    )


# ---------------------------------------------------------------------------
# Distance utility (haversine)
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
