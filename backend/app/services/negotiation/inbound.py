"""Inbound MailPit sweep — runs at end-of-tick.

After the per-neg dispatch loop, we poll MailPit for any vendor replies
addressed to `tavi+{work_order_id}@tavi.local` and hand them to the DB as
`NegotiationMessage` rows with `sender=VENDOR`. Each email is matched
back to a negotiation via the From address → `Vendor.email` → the one
negotiation for (work_order, vendor).

MailPit is advisory — if it's down or returns nothing, this is a no-op
and the existing DB-only simulator path stays correct.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ...enums import MessageChannel, MessageSender
from ...models import Negotiation, Vendor
from . import mailpit, messages

logger = logging.getLogger(__name__)


def sweep(db: Session, work_order_id: str, iteration: int) -> int:
    """Pull new vendor replies for this work order out of MailPit and into
    the DB. Returns the number of messages persisted.

    Safe to call every tick. MailPit unavailability → 0. Unknown sender
    (email From not on any vendor) → skip that message, leave it unread in
    MailPit so it surfaces in the UI as an unreconciled incoming mail.
    """
    try:
        records = mailpit.fetch_unread_for_tavi(work_order_id)
    except mailpit.MailpitUnavailable as e:
        logger.debug("Inbound sweep skipped for wo=%s: %s", work_order_id, e)
        return 0

    if not records:
        return 0

    # Resolve vendor addresses → negotiation rows for this work order.
    # Cache once: most ticks will touch the same vendor set repeatedly.
    neg_by_vendor_email = _vendor_email_to_negotiation(db, work_order_id)

    written = 0
    for r in records:
        neg = neg_by_vendor_email.get(r.from_addr.lower())
        if neg is None:
            logger.info(
                "Inbound MailPit message from %s has no matching negotiation on wo=%s — leaving unread",
                r.from_addr, work_order_id,
            )
            continue

        content: dict[str, Any] = {"text": r.text or ""}
        if r.subject:
            content["subject"] = r.subject

        messages.append_message(
            db, neg,
            sender=MessageSender.VENDOR,
            channel=MessageChannel.EMAIL,
            iteration=iteration,
            content=content,
        )
        mailpit.mark_read(r.id)
        written += 1

    if written:
        db.flush()
    return written


def _vendor_email_to_negotiation(db: Session, work_order_id: str) -> dict[str, Negotiation]:
    """Build a {lowercased_vendor_email → Negotiation} index for one work
    order. Used to route incoming MailPit emails to their negotiation row."""
    rows = (
        db.query(Negotiation, Vendor)
        .join(Vendor, Vendor.place_id == Negotiation.vendor_place_id)
        .filter(Negotiation.work_order_id == work_order_id)
        .all()
    )
    out: dict[str, Negotiation] = {}
    for neg, vendor in rows:
        if vendor.email:
            out[vendor.email.lower()] = neg
    return out
