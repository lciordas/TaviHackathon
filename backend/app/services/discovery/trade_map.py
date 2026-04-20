"""Trade → Google Places search-strategy registry.

Only `plumber` and `electrician` are first-class type tags in the new Places
API. The other four trades use `searchText` with a keyword query — the new
API rejects `general_contractor` / `hvac_contractor` outright (400
INVALID_ARGUMENT), so we don't try.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ...enums import Trade


Strategy = Literal["searchNearby", "searchText"]


@dataclass(frozen=True)
class SearchSpec:
    strategy: Strategy
    included_types: list[str] = field(default_factory=list)  # for searchNearby
    text_query: str = ""  # for searchText, will be appended with location context
    name_keywords: list[str] = field(default_factory=list)  # post-filter on displayName


_TRADE_SPECS: dict[Trade, SearchSpec] = {
    Trade.PLUMBING: SearchSpec(
        strategy="searchNearby",
        included_types=["plumber"],
    ),
    Trade.ELECTRICAL: SearchSpec(
        strategy="searchNearby",
        included_types=["electrician"],
    ),
    Trade.HVAC: SearchSpec(
        strategy="searchText",
        text_query="commercial HVAC contractor",
        name_keywords=["hvac", "heating", "cooling", "air condition", "a/c", "mechanical"],
    ),
    Trade.HANDYMAN: SearchSpec(
        strategy="searchText",
        text_query="commercial handyman services",
        name_keywords=["handyman", "home repair", "property", "maintenance", "craftsman"],
    ),
    Trade.LAWNCARE: SearchSpec(
        strategy="searchText",
        text_query="commercial lawn care",
    ),
    Trade.APPLIANCE_REPAIR: SearchSpec(
        strategy="searchText",
        text_query="commercial appliance repair",
    ),
}


def spec_for(trade: Trade) -> SearchSpec:
    return _TRADE_SPECS[trade]


def name_matches_keywords(display_name: str, keywords: list[str]) -> bool:
    """Case-insensitive substring match on any keyword. Empty keywords = always pass."""
    if not keywords:
        return True
    needle = display_name.lower()
    return any(k.lower() in needle for k in keywords)
