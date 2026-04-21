"""Tavi Coordinator — real implementation.

Single public entrypoint `run_turn`. Stateless: each invocation builds a
fresh Anthropic request from the DB state and the static system prompt, runs
a small tool-use loop (capped at _MAX_ITERATIONS), and applies tool effects
to the DB as it goes.

The scheduler commits; this function only flushes. That lets the scheduler
aggregate all events from a tick into a single transaction.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from anthropic import Anthropic
from sqlalchemy.orm import Session

from ...config import settings
from ...enums import NegotiationState
from ...models import Negotiation, Vendor, WorkOrder
from ..discovery.scoring import haversine_miles
from . import messages, pitch, tools
from .prompts import (
    COORDINATOR_SYSTEM_PROMPT,
    pick_preferred_channel,
    render_coordinator_context,
)

logger = logging.getLogger(__name__)


_client: Optional[Anthropic] = None


def _anthropic() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


_MAX_ITERATIONS = 4  # enough headroom for send + record_facts + record_quote on one turn


def run_turn(
    db: Session,
    *,
    negotiation: Negotiation,
    work_order: WorkOrder,
    vendor: Vendor,
    iteration: int,
    quote_action: Optional[str] = None,
) -> dict[str, Any]:
    """Produce the coordinator's action for one turn.

    Returns a summary dict for the tick result:
      {"message_id": first_outbound_message_id | None, "tool_calls": [names...]}
    """
    preferred = pick_preferred_channel(vendor)

    # PROSPECTING opens with a shared pitch template — one LLM call per work
    # order, reused across every vendor. Skip the coordinator tool-use loop.
    if negotiation.state == NegotiationState.PROSPECTING and preferred == "email":
        return _send_pitch_from_template(
            db, negotiation=negotiation, work_order=work_order,
            vendor=vendor, iteration=iteration,
        )

    distance = _distance_miles(work_order, vendor)
    context = render_coordinator_context(
        work_order=work_order,
        vendor=vendor,
        negotiation=negotiation,
        preferred_channel=preferred,
        quote_action=quote_action,
        distance_miles=distance,
    )

    # Thread first, per-turn context last — the context is the most recent
    # instruction the model should act on, and the trailing user turn also
    # satisfies Sonnet's "messages must end with user" constraint when the
    # thread happens to end with a Tavi (assistant) message.
    api_messages: list[dict[str, Any]] = messages.thread_for_coordinator(db, negotiation.id)
    if api_messages and api_messages[-1]["role"] == "user":
        # Thread ends on a vendor reply. Merge the context onto that final
        # user turn so we don't have two user turns in a row.
        api_messages[-1] = {
            "role": "user",
            "content": api_messages[-1]["content"] + "\n\n" + context,
        }
    else:
        api_messages.append({"role": "user", "content": context})

    tool_calls: list[str] = []
    first_message_id: Optional[str] = None

    for _ in range(_MAX_ITERATIONS):
        resp = _anthropic().messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=COORDINATOR_SYSTEM_PROMPT,
            tools=tools.TOOLS,
            messages=api_messages,
        )

        if resp.stop_reason != "tool_use":
            # Model ended without tool use. Log the text for visibility (it's
            # likely commentary we can discard) and stop.
            text_out = _text_of(resp.content)
            if text_out:
                logger.info("Coordinator emitted text without tool call (neg=%s): %s", negotiation.id, text_out[:200])
            break

        # Record the assistant turn so the next iteration can see the tool
        # request structure.
        api_messages.append({"role": "assistant", "content": resp.content})

        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            outcome = tools.dispatch(
                db,
                negotiation=negotiation,
                iteration=iteration,
                tool_name=block.name,
                tool_input=dict(block.input or {}),
            )
            tool_calls.append(block.name)
            if outcome.success and outcome.detail and "message_id" in outcome.detail and first_message_id is None:
                first_message_id = outcome.detail["message_id"]

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": outcome.message,
                "is_error": not outcome.success,
            })

        api_messages.append({"role": "user", "content": tool_results})

    db.flush()
    return {
        "message_id": first_message_id,
        "tool_calls": tool_calls,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_pitch_from_template(
    db: Session,
    *,
    negotiation: Negotiation,
    work_order: WorkOrder,
    vendor: Vendor,
    iteration: int,
) -> dict[str, Any]:
    """Fill the work order's pitch template for this vendor and dispatch
    `send_email` directly — no per-vendor LLM call.

    The template is generated on first use (first PROSPECTING turn for this
    work order) and cached on `WorkOrder.pitch_template` for every
    subsequent vendor.
    """
    template = pitch.get_or_generate(db, work_order)
    filled = pitch.fill(template, vendor.display_name or "there")

    outcome = tools.dispatch(
        db, negotiation=negotiation, iteration=iteration,
        tool_name="send_email",
        tool_input={"subject": filled["subject"], "body": filled["body"]},
    )
    message_id: Optional[str] = None
    if outcome.success and outcome.detail:
        message_id = outcome.detail.get("message_id")
    else:
        logger.warning("Pitch dispatch failed on neg %s: %s", negotiation.id, outcome.message)

    db.flush()
    return {"message_id": message_id, "tool_calls": ["send_email"]}


def _text_of(content_blocks: list) -> str:
    parts = []
    for b in content_blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n".join(parts)


def _distance_miles(work_order: WorkOrder, vendor: Vendor) -> Optional[float]:
    if None in (work_order.lat, work_order.lng, vendor.lat, vendor.lng):
        return None
    return haversine_miles(work_order.lat, work_order.lng, vendor.lat, vendor.lng)
