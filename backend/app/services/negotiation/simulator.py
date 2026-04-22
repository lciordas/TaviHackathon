"""Vendor simulator — real implementation.

Stateless Anthropic call per vendor turn. Builds the thread in vendor
perspective, LLM drafts one plain-text reply, and we route it back through
the email bus:

  - Thread read: MailPit HTTP API filtered to {to,from}:{vendor.email}.
    The simulator is strictly isolated from the DB — its only view of
    what Tavi has said comes through MailPit.
  - Reply send: SMTP to MailPit (From={vendor.email},
    To=tavi+{wo_id}@tavi.local). The scheduler's end-of-tick inbound sweep
    picks it up and writes it to `negotiation_messages`.

If MailPit is unavailable, both the read and the send fall back to the
DB path that the simulator used before the email bus landed, so the
negotiation can still progress in a degraded mode.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from anthropic import Anthropic
from sqlalchemy.orm import Session

from ...config import settings
from ...enums import MessageChannel, MessageSender
from ...models import Negotiation, Vendor, WorkOrder
from ..discovery.scoring import haversine_miles
from . import mailpit, messages
from .prompts import VENDOR_SIMULATOR_SYSTEM_PROMPT, render_simulator_context

logger = logging.getLogger(__name__)


_client: Optional[Anthropic] = None


def _anthropic() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


_FALLBACK_REPLY = "Sorry, can you resend that?"


def run_turn(
    db: Session,
    *,
    negotiation: Negotiation,
    work_order: WorkOrder,
    vendor: Vendor,
    iteration: int,
) -> dict[str, Any]:
    """Produce a vendor reply for one turn.

    Returns `{"message_id"}`. When MailPit is the path, message_id is
    None — the reply lives in MailPit until the scheduler's inbound
    sweep pulls it into the DB at end-of-tick.
    """
    # Try to source the thread from MailPit. If it fails (MailPit down, or
    # simply no vendor email on file), fall back to the DB thread.
    use_mailpit_in = bool(vendor.email) and settings.mailpit_enabled
    mailpit_thread: list[mailpit.EmailRecord] = []
    if use_mailpit_in:
        try:
            mailpit_thread = mailpit.fetch_vendor_thread(vendor.email or "")
        except mailpit.MailpitUnavailable as e:
            logger.info("MailPit read failed for vendor %s: %s; falling back to DB thread", vendor.email, e)
            use_mailpit_in = False

    last = messages.last_message(db, negotiation.id)
    reply_channel = last.channel if last is not None else MessageChannel.EMAIL

    context = render_simulator_context(
        work_order=work_order,
        vendor=vendor,
        last_message=last,
        distance_miles=_distance_miles(work_order, vendor),
    )

    if use_mailpit_in:
        api_messages = _thread_from_mailpit(mailpit_thread, vendor.email or "")
    else:
        api_messages = messages.thread_for_simulator(db, negotiation.id)

    # Context merges onto the final user turn (or appends a new user turn
    # if the thread ends with an assistant). Same shape as before — Sonnet
    # requires messages[] to end with user.
    if api_messages and api_messages[-1]["role"] == "user":
        api_messages[-1] = {
            "role": "user",
            "content": api_messages[-1]["content"] + "\n\n" + context,
        }
    else:
        api_messages.append({"role": "user", "content": context})

    resp = _anthropic().messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        system=VENDOR_SIMULATOR_SYSTEM_PROMPT,
        messages=api_messages,
    )

    reply_text = _first_text(resp.content) or _FALLBACK_REPLY

    # Send path: prefer MailPit (inbound sweep will write to DB). Fall
    # back to direct DB write if MailPit refuses the SMTP hand-off.
    if vendor.email and settings.mailpit_enabled:
        try:
            subject = _reply_subject(mailpit_thread)
            mailpit.send_vendor_to_tavi(
                work_order_id=work_order.id,
                vendor_email=vendor.email,
                subject=subject,
                body=reply_text,
            )
            return {"message_id": None}  # inbound sweep will persist
        except mailpit.MailpitUnavailable as e:
            logger.info("MailPit vendor→Tavi send failed (neg=%s): %s; falling back to DB", negotiation.id, e)

    # Fallback: legacy direct DB write.
    msg = messages.append_message(
        db, negotiation,
        sender=MessageSender.VENDOR, channel=reply_channel, iteration=iteration,
        content={"text": reply_text},
    )
    db.flush()
    return {"message_id": msg.id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _thread_from_mailpit(records: list[mailpit.EmailRecord], vendor_email: str) -> list[dict[str, Any]]:
    """Convert a MailPit-sourced email list into Anthropic messages[] from
    the vendor's perspective: vendor's own emails → assistant, Tavi's →
    user. Each turn is prefixed with `[email]` to match the DB-path
    rendering so the LLM's register guidance still applies."""
    out: list[dict[str, Any]] = []
    for m in records:
        # Identify vendor vs Tavi purely by From address.
        if m.from_addr == vendor_email:
            role = "assistant"
        else:
            role = "user"
        body = m.text or ""
        if m.subject:
            body = f"Subject: {m.subject}\n\n{body}"
        out.append({"role": role, "content": f"[email] {body}"})
    return out


def _reply_subject(records: list[mailpit.EmailRecord]) -> str:
    """Prefix the most recent Tavi subject with `Re:` for the reply."""
    for m in reversed(records):
        # Most recent first (records are oldest-first).
        if not mailpit.is_tavi_address(m.from_addr) and not m.from_addr.startswith("tavi+"):
            continue
        subj = m.subject or ""
        return subj if subj.lower().startswith("re:") else f"Re: {subj}"
    return "Re: service request"


def _first_text(content_blocks) -> str:
    for b in content_blocks:
        if getattr(b, "type", None) == "text":
            return b.text.strip()
    return ""


def _distance_miles(work_order: WorkOrder, vendor: Vendor) -> Optional[float]:
    if None in (work_order.lat, work_order.lng, vendor.lat, vendor.lng):
        return None
    return haversine_miles(work_order.lat, work_order.lng, vendor.lat, vendor.lng)
