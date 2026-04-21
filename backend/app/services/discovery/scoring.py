"""Scoring formulas for vendor discovery + subpart-3 ranking.

Two scores live here, with very different lifecycles:

1. `compute_cumulative` — the objective vendor score (stable across customers).
   Runs at discovery time and is written to `Vendor.cumulative_score`.
   Inputs: Google rating + review count, BBB grade + complaint resolution,
   tenure-in-business. Signals that go missing drop out; remaining weights
   renormalize.

2. `compute_subjective` — the per-order ranking score.
   DOES NOT RUN at discovery time. It requires a vendor quote
   (`Negotiation.quoted_price_cents`), which only exists after subpart 3's
   outreach agent has contacted a vendor and received firm terms. Takes a
   `RankingWeights` argument so the facility manager can flip between
   price-leaning and quality-leaning views on the same set of quotes.
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


# ---------------------------------------------------------------------------
# Cumulative (objective) score — runs at discovery time
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
# Subjective (per-order) ranking — runs in subpart 3 after a quote arrives
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RankingWeights:
    """How to balance vendor quality vs quote price.

    Weights sum to 1.0 (a small floating-point tolerance is allowed). The
    facility manager can flip to a different profile in the UI to re-rank the
    same set of quoted vendors — e.g., from the urgency default toward
    price-leaning or quality-leaning.

    Future: a third `speed` dimension will land alongside these once subpart 3
    captures vendor-proposed schedules. Keep additions backwards-compatible.
    """

    quality: float
    price: float

    def __post_init__(self) -> None:
        total = self.quality + self.price
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"RankingWeights must sum to ~1.0; got quality={self.quality} + price={self.price} = {total}"
            )


DEFAULT_WEIGHTS_BY_URGENCY: dict[Urgency, RankingWeights] = {
    # Emergencies — don't cheap out in a crisis; quality dominates.
    Urgency.EMERGENCY: RankingWeights(quality=0.80, price=0.20),
    # Urgent — still quality-first, but some price sensitivity.
    Urgency.URGENT: RankingWeights(quality=0.65, price=0.35),
    # Scheduled — balanced. FM has time to shop around.
    Urgency.SCHEDULED: RankingWeights(quality=0.50, price=0.50),
    # Flexible — patient; take the best deal.
    Urgency.FLEXIBLE: RankingWeights(quality=0.35, price=0.65),
}


# Named presets for FM overrides — served to the UI as the "re-rank by X" buttons.
PRESET_WEIGHTS: dict[str, RankingWeights] = {
    "balanced": RankingWeights(quality=0.50, price=0.50),
    "quality_leaning": RankingWeights(quality=0.75, price=0.25),
    "price_leaning": RankingWeights(quality=0.25, price=0.75),
    "quality_only": RankingWeights(quality=1.00, price=0.00),
    "price_only": RankingWeights(quality=0.00, price=1.00),
}


def default_weights_for(urgency: Urgency) -> RankingWeights:
    """Starting weight profile, before any FM override. Keyed to urgency."""
    return DEFAULT_WEIGHTS_BY_URGENCY[urgency]


def price_fit(quote_cents: int, budget_cap_cents: int) -> float:
    """Map a vendor's quote against the work-order budget cap to a [0, 1] fit.

    - 1.0 at quote <= 50% of budget (great deal)
    - linearly decays between 0.5x and 2.0x budget
    - 0.0 at quote >= 2x budget (well over)

    Quotes that exceed budget still score nonzero so a "slightly over" bid
    isn't auto-disqualified — the FM can still see it and decide.
    """
    if budget_cap_cents <= 0:
        return 0.5  # unknown budget — neutral
    ratio = quote_cents / budget_cap_cents
    if ratio <= 0.5:
        return 1.0
    if ratio >= 2.0:
        return 0.0
    return (2.0 - ratio) / 1.5


@dataclass
class SubjectiveResult:
    score: float                                    # in [0, 1]
    breakdown: dict


def compute_subjective(
    *,
    cumulative_score: float,
    quote_cents: int,
    budget_cap_cents: int,
    weights: RankingWeights,
) -> SubjectiveResult:
    """Blend objective quality with quote price under a given weight profile.

    Called by subpart 3, once a Negotiation has a quote_cents set. The FM sees
    a ranked list produced with the urgency's default weights and can re-rank
    with a different weight profile (e.g. PRESET_WEIGHTS["price_leaning"])
    without new vendor interaction.
    """
    price_fit_val = price_fit(quote_cents, budget_cap_cents)
    contributions = {
        "quality": cumulative_score * weights.quality,
        "price": price_fit_val * weights.price,
    }
    score = sum(contributions.values())
    score = max(0.0, min(1.0, score))
    return SubjectiveResult(
        score=round(score, 4),
        breakdown={
            "weights": {"quality": weights.quality, "price": weights.price},
            "signals": {
                "cumulative_score": round(cumulative_score, 3),
                "price_fit": round(price_fit_val, 3),
                "quote_cents": quote_cents,
                "budget_cap_cents": budget_cap_cents,
            },
            "contributions": {k: round(v, 4) for k, v in contributions.items()},
        },
    )


# ---------------------------------------------------------------------------
# Distance utility (used by filters for the 20-mile hard gate)
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
