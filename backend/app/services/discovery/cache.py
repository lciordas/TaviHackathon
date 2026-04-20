"""SQLAlchemy helpers for the vendors cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ...models import Vendor

CACHE_TTL = timedelta(days=30)


def get_vendor(db: Session, place_id: str) -> Optional[Vendor]:
    return db.get(Vendor, place_id)


def get_vendors(db: Session, place_ids: Iterable[str]) -> dict[str, Vendor]:
    ids = list(place_ids)
    if not ids:
        return {}
    rows = db.query(Vendor).filter(Vendor.place_id.in_(ids)).all()
    return {v.place_id: v for v in rows}


def is_google_fresh(vendor: Vendor) -> bool:
    if vendor.google_fetched_at is None:
        return False
    fetched = vendor.google_fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched) < CACHE_TTL


def upsert_google(db: Session, payload: dict) -> Vendor:
    """Insert/update Google-sourced fields. Caller commits."""
    place_id = payload["place_id"]
    v = db.get(Vendor, place_id)
    if v is None:
        v = Vendor(place_id=place_id)
        db.add(v)
    for k, val in payload.items():
        if k == "place_id":
            continue
        setattr(v, k, val)
    return v


def upsert_bbb(db: Session, place_id: str, payload: dict) -> Optional[Vendor]:
    """Update BBB fields on an existing vendor row. Caller commits."""
    v = db.get(Vendor, place_id)
    if v is None:
        return None
    for k, val in payload.items():
        setattr(v, k, val)
    v.bbb_fetched_at = datetime.now(timezone.utc)
    return v
