"""Geocode a WorkOrder's address into lat/lng using Google Places searchText.

Used only when the WorkOrder doesn't already have lat/lng. Caller persists
the result back onto the WorkOrder row so we don't re-geocode.
"""
from __future__ import annotations

import logging
from typing import Optional

from ...models import WorkOrder
from .places_client import PlacesClient


logger = logging.getLogger(__name__)


def assemble_query(work_order: WorkOrder) -> str:
    parts = [work_order.address_line, work_order.city, work_order.state, work_order.zip]
    return " ".join(p for p in parts if p)


def geocode(work_order: WorkOrder, client: Optional[PlacesClient] = None) -> Optional[tuple[float, float]]:
    """Return (lat, lng) from a Places searchText hit, or None if nothing matched."""
    query = assemble_query(work_order)
    if not query:
        return None

    own = client is None
    c = client or PlacesClient()
    try:
        results = c.search_text(text_query=query, max_results=1, ids_only=False)
    finally:
        if own:
            c.close()

    if not results:
        logger.info("Geocode: no Places hits for %r", query)
        return None
    loc = results[0].get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)
