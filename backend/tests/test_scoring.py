"""Scoring formulas — Bayesian rating, cumulative composite, quote-aware subjective."""
from __future__ import annotations

import pytest

from app.enums import Urgency
from app.services.discovery.scoring import (
    DEFAULT_WEIGHTS_BY_URGENCY,
    PRESET_WEIGHTS,
    RankingWeights,
    bayes_rating,
    compute_cumulative,
    compute_subjective,
    default_weights_for,
    haversine_miles,
    price_fit,
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
# Cumulative (objective) score
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
    assert 0.8 < res.score < 0.9


def test_cumulative_handles_zero_complaints():
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
# RankingWeights + defaults
# ---------------------------------------------------------------------------

def test_ranking_weights_must_sum_to_one():
    RankingWeights(quality=0.5, price=0.5)
    RankingWeights(quality=1.0, price=0.0)
    with pytest.raises(ValueError):
        RankingWeights(quality=0.7, price=0.7)


def test_default_weights_sum_to_one_for_every_urgency():
    for u, w in DEFAULT_WEIGHTS_BY_URGENCY.items():
        assert w.quality + w.price == pytest.approx(1.0), u


def test_preset_weights_sum_to_one():
    for name, w in PRESET_WEIGHTS.items():
        assert w.quality + w.price == pytest.approx(1.0), name


def test_emergency_default_favors_quality_over_price():
    w = default_weights_for(Urgency.EMERGENCY)
    assert w.quality > w.price


def test_flexible_default_favors_price_over_quality():
    w = default_weights_for(Urgency.FLEXIBLE)
    assert w.price > w.quality


# ---------------------------------------------------------------------------
# price_fit
# ---------------------------------------------------------------------------

def test_price_fit_well_under_budget_is_one():
    # Quote at 40% of budget → full 1.0 fit
    assert price_fit(40, 100) == 1.0


def test_price_fit_at_half_budget_is_one():
    assert price_fit(50, 100) == 1.0


def test_price_fit_at_full_budget_decays():
    val = price_fit(100, 100)
    assert 0.0 < val < 1.0


def test_price_fit_at_double_budget_is_zero():
    assert price_fit(200, 100) == 0.0


def test_price_fit_over_double_is_still_zero():
    assert price_fit(500, 100) == 0.0


def test_price_fit_zero_budget_is_neutral():
    assert price_fit(100, 0) == 0.5


# ---------------------------------------------------------------------------
# compute_subjective (quote-aware)
# ---------------------------------------------------------------------------

def test_compute_subjective_rewards_cheaper_quote():
    cheap = compute_subjective(
        cumulative_score=0.70, quote_cents=50_00, budget_cap_cents=100_00,
        weights=RankingWeights(quality=0.5, price=0.5),
    )
    expensive = compute_subjective(
        cumulative_score=0.70, quote_cents=150_00, budget_cap_cents=100_00,
        weights=RankingWeights(quality=0.5, price=0.5),
    )
    assert cheap.score > expensive.score


def test_compute_subjective_weights_shift_outcome():
    # Quality-tilted weights favor the high-quality vendor; price-tilted weights
    # swing toward the cheap vendor.
    high_quality_expensive = dict(cumulative_score=0.90, quote_cents=90_00, budget_cap_cents=100_00)
    low_quality_cheap = dict(cumulative_score=0.40, quote_cents=50_00, budget_cap_cents=100_00)

    quality_lean = PRESET_WEIGHTS["quality_leaning"]
    price_lean = PRESET_WEIGHTS["price_leaning"]

    hq_under_quality = compute_subjective(weights=quality_lean, **high_quality_expensive)
    lq_under_quality = compute_subjective(weights=quality_lean, **low_quality_cheap)
    assert hq_under_quality.score > lq_under_quality.score

    hq_under_price = compute_subjective(weights=price_lean, **high_quality_expensive)
    lq_under_price = compute_subjective(weights=price_lean, **low_quality_cheap)
    assert lq_under_price.score > hq_under_price.score


def test_compute_subjective_breakdown_shape():
    res = compute_subjective(
        cumulative_score=0.75, quote_cents=80_00, budget_cap_cents=100_00,
        weights=RankingWeights(quality=0.6, price=0.4),
    )
    assert "weights" in res.breakdown
    assert "signals" in res.breakdown
    assert "contributions" in res.breakdown
    assert res.breakdown["signals"]["quote_cents"] == 80_00
    assert res.breakdown["signals"]["cumulative_score"] == 0.75


# ---------------------------------------------------------------------------
# Distance utility (still used by the radius hard filter)
# ---------------------------------------------------------------------------

def test_haversine_dallas_to_fort_worth():
    miles = haversine_miles(32.7767, -96.7970, 32.7555, -97.3308)
    assert 28 < miles < 33
