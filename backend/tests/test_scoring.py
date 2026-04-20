"""Scoring formulas — Bayesian rating, cumulative composite, subjective per-order."""
from __future__ import annotations

import pytest

from app.enums import Urgency
from app.services.discovery.scoring import (
    BAYES_PRIOR_RATING,
    BAYES_PRIOR_STRENGTH,
    SUBJECTIVE_WEIGHTS,
    bayes_rating,
    compute_cumulative,
    compute_subjective,
    haversine_miles,
)


# ---------------------------------------------------------------------------
# Bayesian rating
# ---------------------------------------------------------------------------

def test_bayes_anchors_low_review_count_toward_prior():
    # 5.0 with only 3 reviews should land near 4.0 prior, not at 5.0.
    score = bayes_rating(5.0, 3)
    assert 4.0 < score < 4.2


def test_bayes_high_review_count_pulls_toward_actual_rating():
    # 4.6 with 1000 reviews should be much closer to 4.6 than the prior.
    score = bayes_rating(4.6, 1000)
    assert 4.55 < score < 4.61


def test_bayes_returns_none_for_missing_rating():
    assert bayes_rating(None, 100) is None


# ---------------------------------------------------------------------------
# Cumulative score — full signals
# ---------------------------------------------------------------------------

def test_cumulative_premium_vendor_high_score():
    res = compute_cumulative(
        google_rating=4.7,
        google_user_rating_count=600,
        bbb_grade="A+",
        bbb_complaints_total=8,
        bbb_complaints_resolved=8,
        years_in_business=20,
    )
    assert res.score > 0.85
    assert res.bayes_rating_1_to_5 is not None
    assert 4.5 < res.bayes_rating_1_to_5 < 4.7


def test_cumulative_low_quality_vendor_low_score():
    res = compute_cumulative(
        google_rating=3.1,
        google_user_rating_count=200,
        bbb_grade="D",
        bbb_complaints_total=40,
        bbb_complaints_resolved=10,
        years_in_business=4,
    )
    assert res.score < 0.4


# ---------------------------------------------------------------------------
# Cumulative score — missing BBB → renormalized to rating only
# ---------------------------------------------------------------------------

def test_cumulative_no_bbb_falls_back_to_rating_only():
    res = compute_cumulative(
        google_rating=4.4,
        google_user_rating_count=300,
        bbb_grade=None,
        bbb_complaints_total=None,
        bbb_complaints_resolved=None,
        years_in_business=None,
    )
    weights = res.breakdown["weights_applied"]
    assert set(weights.keys()) == {"bayes_rating"}
    assert weights["bayes_rating"] == pytest.approx(1.0)
    # bayes ≈ 4.34 → normalized ≈ 0.835
    assert 0.8 < res.score < 0.9


def test_cumulative_handles_zero_complaints():
    # 0 complaints → resolution rate = 1.0
    res = compute_cumulative(
        google_rating=4.5,
        google_user_rating_count=200,
        bbb_grade="A",
        bbb_complaints_total=0,
        bbb_complaints_resolved=None,
        years_in_business=10,
    )
    assert res.breakdown["signals"]["resolution_rate"] == 1.0


# ---------------------------------------------------------------------------
# Weight profiles
# ---------------------------------------------------------------------------

def test_subjective_weights_sum_to_one_for_each_urgency():
    for urgency, weights in SUBJECTIVE_WEIGHTS.items():
        assert sum(weights.values()) == pytest.approx(1.0), urgency


def test_subjective_emergency_rewards_distance_and_24_7():
    # Same vendor (high cumulative, close, 24/7) under each urgency.
    base = dict(
        cumulative_score=0.85,
        distance_miles=2.0,
        emergency_service_24_7=True,
        price_level=2,
    )
    emergency = compute_subjective(urgency=Urgency.EMERGENCY, **base)
    scheduled = compute_subjective(urgency=Urgency.SCHEDULED, **base)
    # Emergency rewards distance + 24/7 → both contribute meaningfully.
    em_contrib = emergency.breakdown["contributions"]
    assert em_contrib["distance_fit"] > em_contrib["cumulative"] * 0.5
    assert em_contrib["h24_7_fit"] > 0
    # Scheduled puts most weight on cumulative, none on 24/7.
    sc_contrib = scheduled.breakdown["contributions"]
    assert sc_contrib["cumulative"] > 0.4
    assert sc_contrib["h24_7_fit"] == 0.0


def test_subjective_24_7_only_rewarded_when_urgency_warrants():
    # 24/7 under SCHEDULED should not contribute (urgency doesn't warrant it).
    res = compute_subjective(
        cumulative_score=0.85, urgency=Urgency.SCHEDULED, distance_miles=5.0,
        emergency_service_24_7=True, price_level=2,
    )
    assert res.breakdown["signals"]["h24_7_fit"] == 0.0


def test_subjective_distance_decays_linearly():
    near = compute_subjective(
        cumulative_score=0.5, urgency=Urgency.EMERGENCY, distance_miles=2.0,
        emergency_service_24_7=False, price_level=2,
    )
    far = compute_subjective(
        cumulative_score=0.5, urgency=Urgency.EMERGENCY, distance_miles=18.0,
        emergency_service_24_7=False, price_level=2,
    )
    assert near.score > far.score


# ---------------------------------------------------------------------------
# Distance utility
# ---------------------------------------------------------------------------

def test_haversine_dallas_to_fort_worth():
    # ~30 miles
    miles = haversine_miles(32.7767, -96.7970, 32.7555, -97.3308)
    assert 28 < miles < 33
