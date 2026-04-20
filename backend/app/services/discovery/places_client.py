"""Thin client for Google Places API (new) — searchNearby, searchText, getPlace.

Field masks are constants here so the SKU tier is auditable. The IDs-only
search masks are deliberately cheap (free tier); the details mask requests
exactly the Enterprise fields we use and nothing else (no photos, no review text).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ...config import settings


logger = logging.getLogger(__name__)


PLACES_BASE = "https://places.googleapis.com/v1"

# IDs-only field masks → free, unlimited.
SEARCH_NEARBY_MASK = "places.id"
SEARCH_TEXT_IDS_MASK = "places.id"
SEARCH_TEXT_GEOCODE_MASK = "places.id,places.location"

# Enterprise-tier details mask. Ordered for readability.
DETAILS_MASK = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "location",
    "types",
    "businessStatus",
    "rating",
    "userRatingCount",
    "regularOpeningHours",
    "utcOffsetMinutes",
    "internationalPhoneNumber",
    "websiteUri",
    "priceLevel",
])

# Cheap Essentials-tier mask for the autocomplete-select flow: we only need
# the address components + lat/lng, not reviews/hours/phone.
GEOCODE_DETAILS_MASK = "id,addressComponents,location,formattedAddress"

AUTOCOMPLETE_MASK = (
    "suggestions.placePrediction.placeId,"
    "suggestions.placePrediction.structuredFormat"
)


class PlacesError(RuntimeError):
    pass


class PlacesClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 10.0):
        self.api_key = api_key or settings.google_places_api_key
        if not self.api_key:
            raise PlacesError("GOOGLE_PLACES_API_KEY is not set")
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> "PlacesClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- searches ------------------------------------------------------

    def search_nearby(
        self,
        *,
        lat: float,
        lng: float,
        radius_m: int,
        included_types: list[str],
        max_results: int = 20,
    ) -> list[str]:
        """Return up to `max_results` place_ids near (lat, lng)."""
        body = {
            "includedTypes": included_types,
            "maxResultCount": min(max_results, 20),
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m),
                }
            },
        }
        data = self._post("/places:searchNearby", body, mask=SEARCH_NEARBY_MASK)
        return [p["id"] for p in data.get("places", [])]

    def search_text(
        self,
        *,
        text_query: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius_m: Optional[int] = None,
        max_results: int = 20,
        ids_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Returns a list of {id, location?} entries.

        If ids_only=True (default): cheapest mask, just place_ids.
        If False: includes location for geocoding fallback.
        """
        body: dict[str, Any] = {"textQuery": text_query, "maxResultCount": min(max_results, 20)}
        if lat is not None and lng is not None and radius_m:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m),
                }
            }
        mask = SEARCH_TEXT_IDS_MASK if ids_only else SEARCH_TEXT_GEOCODE_MASK
        data = self._post("/places:searchText", body, mask=mask)
        return data.get("places", [])

    # ---- details -------------------------------------------------------

    def get_place(self, place_id: str) -> dict[str, Any]:
        """Fetch one place's details. Bills Enterprise tier (1k/mo free)."""
        return self._get(f"/places/{place_id}", mask=DETAILS_MASK)

    def get_address_details(self, place_id: str) -> dict[str, Any]:
        """Fetch just the address components + lat/lng. Cheaper Essentials tier."""
        return self._get(f"/places/{place_id}", mask=GEOCODE_DETAILS_MASK)

    # ---- autocomplete --------------------------------------------------

    def autocomplete(
        self,
        *,
        input_text: str,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius_m: int = 50000,
    ) -> list[dict[str, str]]:
        """Return a list of suggestion dicts: {place_id, primary_text, secondary_text}."""
        body: dict[str, Any] = {
            "input": input_text,
            "includedRegionCodes": ["us"],
        }
        if lat is not None and lng is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius_m),
                }
            }
        data = self._post("/places:autocomplete", body, mask=AUTOCOMPLETE_MASK)

        out: list[dict[str, str]] = []
        for s in data.get("suggestions", []):
            pp = s.get("placePrediction")
            if not pp:
                continue
            place_id = pp.get("placeId")
            if not place_id:
                continue
            sf = pp.get("structuredFormat") or {}
            primary = (sf.get("mainText") or {}).get("text") or ""
            secondary = (sf.get("secondaryText") or {}).get("text") or ""
            out.append({
                "place_id": place_id,
                "primary_text": primary,
                "secondary_text": secondary,
            })
        return out

    # ---- internals -----------------------------------------------------

    def _post(self, path: str, body: dict, *, mask: str) -> dict[str, Any]:
        return self._call("POST", path, mask=mask, json=body)

    def _get(self, path: str, *, mask: str) -> dict[str, Any]:
        return self._call("GET", path, mask=mask)

    def _call(self, method: str, path: str, *, mask: str, **kwargs: Any) -> dict[str, Any]:
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": mask,
            "Content-Type": "application/json",
        }
        url = PLACES_BASE + path
        try:
            resp = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise PlacesError(f"Network error calling Places: {e}") from e

        if resp.status_code >= 400:
            logger.error("Places API %s %s -> %s: %s", method, path, resp.status_code, resp.text[:500])
            raise PlacesError(f"Places API {resp.status_code}: {resp.text[:200]}")
        return resp.json()


# ---------------------------------------------------------------------------
# Helpers to translate a raw `places/{id}` payload into our Vendor row shape.
# ---------------------------------------------------------------------------

def parse_address_components(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Translate a place-details payload (addressComponents + location) into
    our structured address shape. Returns None if required components are missing.
    """
    components = payload.get("addressComponents") or []
    by_type: dict[str, dict] = {}
    for c in components:
        for t in c.get("types") or []:
            by_type[t] = c

    def long_of(*keys: str) -> Optional[str]:
        for k in keys:
            if k in by_type:
                return by_type[k].get("longText") or by_type[k].get("shortText")
        return None

    def short_of(*keys: str) -> Optional[str]:
        for k in keys:
            if k in by_type:
                return by_type[k].get("shortText") or by_type[k].get("longText")
        return None

    number = long_of("street_number")
    street = long_of("route")
    address_line = " ".join(p for p in (number, street) if p) or None

    city = long_of("locality", "sublocality", "postal_town", "administrative_area_level_3")
    state = short_of("administrative_area_level_1")
    zip_code = long_of("postal_code")

    loc = payload.get("location") or {}
    lat = loc.get("latitude")
    lng = loc.get("longitude")

    if not address_line or not city or not state or not zip_code or lat is None or lng is None:
        return None

    return {
        "address_line": address_line,
        "city": city,
        "state": state,
        "zip": zip_code,
        "lat": float(lat),
        "lng": float(lng),
        "formatted_address": payload.get("formattedAddress") or "",
    }


def detect_24_7(regular_opening_hours: Optional[dict]) -> bool:
    if not regular_opening_hours:
        return False
    periods = regular_opening_hours.get("periods") or []
    if len(periods) != 1:
        return False
    p = periods[0]
    return bool((p.get("open") or p.get("openTime"))) and not (p.get("close") or p.get("closeTime"))


def details_to_vendor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate Places `getPlace` JSON into kwargs for Vendor row upsert."""
    loc = payload.get("location") or {}
    display_name = payload.get("displayName") or {}
    if isinstance(display_name, dict):
        display_name = display_name.get("text") or ""
    rating = payload.get("rating")
    user_count = payload.get("userRatingCount")
    hours = payload.get("regularOpeningHours")

    # priceLevel comes back as a string enum like "PRICE_LEVEL_MODERATE";
    # map to the int convention used elsewhere (0..4).
    price_map = {
        "PRICE_LEVEL_FREE": 0,
        "PRICE_LEVEL_INEXPENSIVE": 1,
        "PRICE_LEVEL_MODERATE": 2,
        "PRICE_LEVEL_EXPENSIVE": 3,
        "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    }
    price_raw = payload.get("priceLevel")
    if isinstance(price_raw, str):
        price_level = price_map.get(price_raw)
    elif isinstance(price_raw, int):
        price_level = price_raw
    else:
        price_level = None

    return {
        "place_id": payload["id"],
        "display_name": display_name,
        "formatted_address": payload.get("formattedAddress"),
        "lat": loc.get("latitude") or 0.0,
        "lng": loc.get("longitude") or 0.0,
        "types": payload.get("types") or [],
        "business_status": payload.get("businessStatus"),
        "google_rating": rating,
        "google_user_rating_count": user_count,
        "regular_opening_hours": hours,
        "utc_offset_minutes": payload.get("utcOffsetMinutes"),
        "international_phone_number": payload.get("internationalPhoneNumber"),
        "website_uri": payload.get("websiteUri"),
        "price_level": price_level,
        "emergency_service_24_7": detect_24_7(hours),
    }
