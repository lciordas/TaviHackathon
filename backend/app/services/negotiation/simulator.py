"""Vendor simulator — real implementation.

Stateless Anthropic call per vendor turn. Reads the vendor's profile +
persona markdown + work order + thread (in vendor perspective, tool outputs
filtered) and returns a single natural-language message. No tools, no
structured output.

The scheduler commits; this function only flushes.
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
from . import messages
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
    """Produce a vendor reply for one turn. Returns `{"message_id"}`.

    Channel mirrors the last Tavi message (the vendor replies in the same
    medium they were reached on). Falls back to email if for some reason
    the thread has no Tavi message yet.
    """
    last = messages.last_message(db, negotiation.id)
    reply_channel = last.channel if last is not None else MessageChannel.EMAIL

    context = render_simulator_context(
        work_order=work_order,
        vendor=vendor,
        last_message=last,
        distance_miles=_distance_miles(work_order, vendor),
    )

    # Thread first (in vendor perspective), context merged onto the final
    # user turn. Putting context at the start would create back-to-back user
    # turns on the very first reply (thread = [user: tavi pitch], context =
    # [user: persona + wo]), which Sonnet rejects.
    api_messages: list[dict[str, Any]] = messages.thread_for_simulator(db, negotiation.id)
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

def _first_text(content_blocks) -> str:
    for b in content_blocks:
        if getattr(b, "type", None) == "text":
            return b.text.strip()
    return ""


def _distance_miles(work_order: WorkOrder, vendor: Vendor) -> Optional[float]:
    if None in (work_order.lat, work_order.lng, vendor.lat, vendor.lng):
        return None
    return haversine_miles(work_order.lat, work_order.lng, vendor.lat, vendor.lng)
