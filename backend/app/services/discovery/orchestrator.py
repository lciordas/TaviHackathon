"""Top-level vendor-discovery flow.

Composes Google Places search → details → BBB enrichment → cumulative scoring →
hard filters → subjective ranking → persistence (Vendor + Negotiation +
DiscoveryRun rows).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ...config import settings
from ...database import SessionLocal
from ...models import DiscoveryEvent, DiscoveryRun, Negotiation, Vendor, WorkOrder
from . import bbb_client, cache, places_client, scoring
from .filters import apply_filters
from .geocoding import geocode
from .trade_map import name_matches_keywords, spec_for


logger = logging.getLogger(__name__)


def _emit_event(
    work_order_id: str,
    kind: str,
    *,
    vendor_name: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """Write one progress row on its own short-lived session.

    Uses a separate session so the row is committed independently of the
    long-running discovery transaction — the frontend sees events the
    moment they happen, even while the main run is still in flight.
    Best-effort: swallow failures rather than blowing up the orchestrator.
    """
    try:
        with SessionLocal() as s:
            s.add(DiscoveryEvent(
                work_order_id=work_order_id,
                kind=kind,
                vendor_name=vendor_name,
                detail=detail,
            ))
            s.commit()
    except Exception:
        logger.exception("failed to emit discovery event (%s) for %s", kind, work_order_id)


CACHED_RUN_WINDOW = timedelta(hours=24)

# Keep the vendor pool small enough that the full negotiation auction stays
# demoable within a handful of ticks. Google's Places API allows up to 20 per
# page; we just take the top N by search relevance.
MAX_CANDIDATES = 12


class DiscoveryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def run_discovery(db: Session, work_order_id: str, *, refresh: bool = False) -> DiscoveryRun:
    """Execute one full discovery pass for a work order.

    Returns the fresh `DiscoveryRun` row (with negotiations cascaded through
    the relationships). Caller is responsible for serializing the response
    via Pydantic schemas.

    Idempotency: if a run exists for this work order within `CACHED_RUN_WINDOW`
    and `refresh=False`, returns the most recent run unchanged.
    """
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None:
        raise DiscoveryError(f"WorkOrder not found: {work_order_id}")

    if not refresh:
        recent = (
            db.query(DiscoveryRun)
            .filter(DiscoveryRun.work_order_id == work_order_id)
            .order_by(DiscoveryRun.created_at.desc())
            .first()
        )
        if recent is not None:
            recent_at = recent.created_at
            if recent_at.tzinfo is None:
                recent_at = recent_at.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - recent_at) < CACHED_RUN_WINDOW:
                logger.info("Returning cached DiscoveryRun %s for work_order %s", recent.id, work_order_id)
                return recent

    if not settings.google_places_api_key:
        raise DiscoveryError("GOOGLE_PLACES_API_KEY is not configured")

    started = time.monotonic()
    pc = places_client.PlacesClient()
    try:
        # 1. Resolve work-order location.
        if work_order.lat is None or work_order.lng is None:
            coords = geocode(work_order, client=pc)
            if coords is None:
                raise DiscoveryError("Could not geocode work order address")
            work_order.lat, work_order.lng = coords
            db.flush()

        # 2. Find candidate place_ids.
        spec = spec_for(work_order.trade)
        radius_m = settings.google_places_default_radius_m

        radius_mi = int(round(radius_m / 1609.344))
        _emit_event(
            work_order.id,
            "search_start",
            detail=(
                f"Searching {work_order.trade.value} vendors near "
                f"{work_order.city or '—'}, {work_order.state or ''} "
                f"({radius_mi}mi radius)"
            ),
        )

        if spec.strategy == "searchNearby":
            candidate_ids = pc.search_nearby(
                lat=work_order.lat,
                lng=work_order.lng,
                radius_m=radius_m,
                included_types=spec.included_types,
                max_results=MAX_CANDIDATES,
            )
        else:  # searchText
            text_q = f"{spec.text_query} near {work_order.city or ''}".strip()
            results = pc.search_text(
                text_query=text_q,
                lat=work_order.lat,
                lng=work_order.lng,
                radius_m=radius_m,
                max_results=MAX_CANDIDATES,
                ids_only=True,
            )
            candidate_ids = [r["id"] for r in results]

        candidate_count = len(candidate_ids)
        _emit_event(
            work_order.id,
            "candidates",
            detail=f"{candidate_count} candidate place{'s' if candidate_count != 1 else ''} found",
        )

        # 3. Pull details for new place_ids; reuse cache for known ones.
        existing = cache.get_vendors(db, candidate_ids)
        cache_hit_count = sum(1 for pid in candidate_ids if pid in existing and cache.is_google_fresh(existing[pid]))
        api_detail_calls = 0

        for pid in candidate_ids:
            v = existing.get(pid)
            if v is not None and cache.is_google_fresh(v):
                _emit_event(
                    work_order.id,
                    "place_cached",
                    vendor_name=v.display_name,
                    detail=v.formatted_address,
                )
                continue
            try:
                payload = pc.get_place(pid)
            except places_client.PlacesError as e:
                logger.warning("Failed to fetch details for %s: %s", pid, e)
                continue
            api_detail_calls += 1
            mapping = places_client.details_to_vendor_payload(payload)
            cache.upsert_google(db, mapping)
            _emit_event(
                work_order.id,
                "place_details",
                vendor_name=mapping.get("display_name"),
                detail=mapping.get("formatted_address"),
            )
        db.flush()

        # 4. Refetch cached vendor rows now that we've upserted.
        all_vendors = cache.get_vendors(db, candidate_ids)

        # 5. Split by trade-name keyword filter. Name-filtered vendors still
        #    get a Negotiation row (marked filtered=True) so the admin + command
        #    center can show why they were excluded — but they skip BBB
        #    enrichment since they're not real candidates for this trade.
        if spec.name_keywords:
            vendors = {pid: v for pid, v in all_vendors.items()
                       if name_matches_keywords(v.display_name, spec.name_keywords)}
            name_filtered = {pid: v for pid, v in all_vendors.items()
                             if pid not in vendors}
        else:
            vendors = all_vendors
            name_filtered = {}

        # 6. BBB enrichment for any eligible vendor that hasn't been scraped yet.
        bbb_scrape_count = 0
        for pid, v in vendors.items():
            if v.bbb_fetched_at is not None:
                continue
            # Prefer Google's address city/state when present; fallback to work order city/state.
            city, state = _city_state_from_address(v.formatted_address) or (work_order.city, work_order.state)
            try:
                profile = bbb_client.fetch_bbb_for_vendor(v.display_name, city, state)
            except Exception as e:  # never crash discovery on BBB failures
                logger.warning("BBB fetch failed for %s: %s", v.display_name, e)
                profile = None
            bbb_scrape_count += 1
            payload: dict = {}
            if profile is not None:
                payload = {
                    "bbb_profile_url": profile.profile_url,
                    "bbb_grade": profile.grade,
                    "bbb_accredited": profile.accredited,
                    "bbb_years_accredited": profile.years_accredited,
                    "bbb_complaints_total": profile.complaints_total,
                    "bbb_complaints_resolved": profile.complaints_resolved,
                    "years_in_business": profile.years_in_business,
                }
                yrs = profile.years_in_business
                bbb_detail = (
                    f"BBB {profile.grade or 'N/A'}"
                    + (f" · {yrs} yrs in business" if yrs else "")
                )
            else:
                bbb_detail = "no BBB profile found"
            cache.upsert_bbb(db, pid, payload)
            _emit_event(
                work_order.id,
                "bbb_scrape",
                vendor_name=v.display_name,
                detail=bbb_detail,
            )
        db.flush()

        _emit_event(
            work_order.id,
            "scoring",
            detail=f"Scoring {len(vendors)} vendor{'s' if len(vendors) != 1 else ''}",
        )

        # 7. Recompute cumulative scores after enrichment. Also score the
        #    name-filtered vendors (pure CPU, no API cost) so the admin UI
        #    can show "we dropped X which had a 4.7 Google rating".
        vendors = cache.get_vendors(db, list(vendors.keys()))
        for v in list(vendors.values()) + list(name_filtered.values()):
            res = scoring.compute_cumulative(
                google_rating=v.google_rating,
                google_user_rating_count=v.google_user_rating_count,
                bbb_grade=v.bbb_grade,
                bbb_complaints_total=v.bbb_complaints_total,
                bbb_complaints_resolved=v.bbb_complaints_resolved,
                years_in_business=v.years_in_business,
            )
            v.cumulative_score = res.score
            v.cumulative_score_breakdown = {
                **res.breakdown,
                "bayes_rating_1_to_5": res.bayes_rating_1_to_5,
            }
        db.flush()

        # 8. Persist DiscoveryRun (need its id to FK negotiations).
        run = DiscoveryRun(
            work_order_id=work_order.id,
            strategy=spec.strategy,
            radius_miles=int(round(radius_m / 1609.344)),
            candidate_count=candidate_count,
            cache_hit_count=cache_hit_count,
            api_detail_calls=api_detail_calls,
            bbb_scrape_count=bbb_scrape_count,
            weight_profile=work_order.urgency.value,
        )
        db.add(run)
        db.flush()

        # 9. Apply hard filters + create Negotiation rows.
        # Subjective ranking is NOT computed here — it runs in subpart 3 once
        # the outreach agent has collected a quote per vendor. Survivors
        # arrive at `prospecting` status with subjective_rank_score / rank /
        # quoted_price_cents all null.
        #
        # Every discovered vendor gets a negotiation row — name-filtered and
        # hard-filtered ones both arrive as filtered=True with a reason
        # string so the admin + command center can explain the exclusion.
        for v in vendors.values():
            bayes = (v.cumulative_score_breakdown or {}).get("bayes_rating_1_to_5") if v.cumulative_score_breakdown else None
            f = apply_filters(work_order, v, bayes)
            neg = Negotiation(
                work_order_id=work_order.id,
                vendor_place_id=v.place_id,
                discovery_run_id=run.id,
                filtered=not f.passed,
                filter_reasons=f.reasons or None,
            )
            db.add(neg)

        if name_filtered:
            name_reason = (
                f"display_name does not match trade keywords: "
                f"{', '.join(spec.name_keywords)}"
            )
            for v in name_filtered.values():
                neg = Negotiation(
                    work_order_id=work_order.id,
                    vendor_place_id=v.place_id,
                    discovery_run_id=run.id,
                    filtered=True,
                    filter_reasons=[name_reason],
                )
                db.add(neg)

        run.duration_ms = int((time.monotonic() - started) * 1000)
        db.commit()

        eligible = sum(1 for v in vendors.values())
        filtered_out_count = len(name_filtered) + sum(
            1 for v in vendors.values()
            if apply_filters(work_order, v, (v.cumulative_score_breakdown or {}).get("bayes_rating_1_to_5") if v.cumulative_score_breakdown else None).passed is False
        )
        _emit_event(
            work_order.id,
            "done",
            detail=(
                f"{eligible} eligible, {filtered_out_count} filtered · "
                f"{run.duration_ms}ms"
            ),
        )
        return run
    finally:
        pc.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _city_state_from_address(formatted_address: Optional[str]) -> Optional[tuple[str, str]]:
    """Parse 'Foo St, Dallas, TX 75207, USA' into ('Dallas', 'TX'). Best-effort."""
    if not formatted_address:
        return None
    parts = [p.strip() for p in formatted_address.split(",")]
    if len(parts) < 3:
        return None
    city = parts[-3]
    state_zip = parts[-2].split()
    if not state_zip:
        return None
    state = state_zip[0]
    if len(state) != 2:
        return None
    return city, state
