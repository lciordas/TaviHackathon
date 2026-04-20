"""Parser tests for the BBB scraper. No live network — uses inline HTML fixtures.

We don't mock the BBB live site; we exercise the parser against representative
HTML so layout shifts get caught at test-time rather than in production.
"""
from __future__ import annotations

from app.services.discovery.bbb_client import _name_similarity, _parse_profile


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def test_name_similarity_strips_corp_suffixes():
    assert _name_similarity("Acme Plumbing LLC", "Acme Plumbing") > 0.85


def test_name_similarity_lowercases():
    assert _name_similarity("HERNANDEZ HVAC", "hernandez hvac") > 0.95


def test_name_similarity_dissimilar_names_low_score():
    assert _name_similarity("Acme Plumbing", "Big Sky Roofing") < 0.4


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------

PREMIUM_PROFILE_HTML = """
<html><body>
  <div class="header">
    <h1>Hernandez Plumbing Co.</h1>
    <p>BBB Accredited Business</p>
    <p>BBB Rating: A+</p>
    <p>Accredited Since: 4/1/2010</p>
  </div>
  <section>
    <h2>Customer Complaints</h2>
    <p>Complaints Closed in last 3 Years: 8</p>
    <p>Complaints Resolved: 7</p>
  </section>
  <section>
    <p>Years in Business: 18</p>
  </section>
</body></html>
"""

LOW_TIER_PROFILE_HTML = """
<html><body>
  <h1>Cheap Plumbers Inc</h1>
  <p>Not BBB Accredited</p>
  <p>BBB Rating: D</p>
  <p>Customer Complaints: 42</p>
  <p>Complaints Resolved: 12</p>
  <p>Years in Business: 3</p>
</body></html>
"""

NO_DATA_PROFILE_HTML = """
<html><body><h1>Unknown Vendor</h1><p>Profile under review.</p></body></html>
"""


def test_parse_premium_profile():
    parsed = _parse_profile(PREMIUM_PROFILE_HTML)
    assert parsed["grade"] == "A+"
    assert parsed["accredited"] is True
    assert parsed["years_accredited"] is not None and parsed["years_accredited"] >= 14
    assert parsed["complaints_total"] == 8
    assert parsed["complaints_resolved"] == 7
    assert parsed["years_in_business"] == 18


def test_parse_low_tier_profile():
    parsed = _parse_profile(LOW_TIER_PROFILE_HTML)
    assert parsed["grade"] == "D"
    assert parsed["accredited"] is False
    assert parsed["complaints_total"] == 42
    assert parsed["complaints_resolved"] == 12
    assert parsed["years_in_business"] == 3


def test_parse_empty_profile_returns_nulls():
    parsed = _parse_profile(NO_DATA_PROFILE_HTML)
    assert parsed["grade"] is None
    assert parsed["accredited"] is None
    assert parsed["complaints_total"] is None
    assert parsed["years_in_business"] is None
