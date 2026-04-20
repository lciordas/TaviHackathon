"""Places autocomplete proxy — keeps the Google key server-side.

Used by the intake UI to populate the work order's address fields via a
Google Places autocomplete widget. Two endpoints:

- POST /intake/places/autocomplete   → keystroke suggestions
- POST /intake/places/select         → resolve one place_id to structured address
"""

import logging

from fastapi import APIRouter, HTTPException

from ..schemas import (
    PlacesAutocompleteRequest,
    PlacesAutocompleteResponse,
    PlacesAutocompleteSuggestion,
    PlacesSelectRequest,
    PlacesSelectResponse,
)
from ..services.discovery.places_client import (
    PlacesClient,
    PlacesError,
    parse_address_components,
)


logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/autocomplete", response_model=PlacesAutocompleteResponse)
def autocomplete(req: PlacesAutocompleteRequest) -> PlacesAutocompleteResponse:
    try:
        with PlacesClient() as client:
            suggestions = client.autocomplete(
                input_text=req.query,
                lat=req.lat,
                lng=req.lng,
            )
    except PlacesError as e:
        logger.warning("autocomplete failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

    return PlacesAutocompleteResponse(
        suggestions=[PlacesAutocompleteSuggestion(**s) for s in suggestions],
    )


@router.post("/select", response_model=PlacesSelectResponse)
def select(req: PlacesSelectRequest) -> PlacesSelectResponse:
    try:
        with PlacesClient() as client:
            payload = client.get_address_details(req.place_id)
    except PlacesError as e:
        logger.warning("select failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

    parsed = parse_address_components(payload)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail="Could not parse a complete US address (need street, city, state, zip) from the selected place.",
        )
    return PlacesSelectResponse(**parsed)
