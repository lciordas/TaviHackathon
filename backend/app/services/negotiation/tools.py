"""Anthropic tool definitions + executors for the Tavi Coordinator.

The JSON schemas live here (passed into `tools=[]` on the Anthropic call),
and so do the dispatchers that apply each call's effects to the DB. Each
dispatcher returns a short dict the model sees as its tool_result — success
or a readable error so the model can correct course on the next iteration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ...enums import MessageChannel, MessageSender, NegotiationState
from ...models import Negotiation, Vendor
from . import mailpit, messages
from .readiness import refresh_ready_to_schedule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas — the array passed to Anthropic
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_email",
        "description": (
            "Send an email to the vendor. Persists a message in the thread "
            "with channel=email. On the first send (any channel), state moves "
            "PROSPECTING → CONTACTED automatically. Use for fuller messages "
            "(3–5 sentences) where a subject line makes sense."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Short subject line."},
                "body": {"type": "string", "description": "Email body in plain prose. No markdown."},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_sms",
        "description": (
            "Send an SMS to the vendor. Persists a message in the thread with "
            "channel=sms. On first send, state moves PROSPECTING → CONTACTED. "
            "Keep to 1–2 lines, never more than ~3 short sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "SMS body."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_phone",
        "description": (
            "Initiate a phone call with an opening utterance (will be read "
            "aloud). Persists a message with channel=phone. The text should "
            "be what you'd say as a first sentence on the call, not a "
            "written memo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "First utterance, spoken-style."},
            },
            "required": ["script"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_quote",
        "description": (
            "Persist a firm quote: the vendor has committed to a specific "
            "price AND a specific date. State moves NEGOTIATING → QUOTED. "
            "Do NOT use for ranges, soft numbers, or 'depends on' quotes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "price_cents": {
                    "type": "integer",
                    "description": "Firm price the vendor committed to, in cents ($1,200 = 120000).",
                },
                "available_at": {
                    "type": "string",
                    "format": "date-time",
                    "description": "ISO 8601 UTC timestamp when the vendor will perform the job.",
                },
            },
            "required": ["price_cents", "available_at"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_facts",
        "description": (
            "Merge freeform facts learned this turn (insurance status, "
            "license number, availability notes, scope constraints, etc.) "
            "into the negotiation's attributes bag. Call in the same turn "
            "you learn the fact. No state change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "object",
                    "description": "Key/value pairs. Keys are snake_case. Example: {\"insurance_verified\": true, \"license_number\": \"TX-12345\"}.",
                    "additionalProperties": True,
                },
            },
            "required": ["facts"],
            "additionalProperties": False,
        },
    },
    {
        "name": "close_negotiation",
        "description": (
            "End the negotiation without a quote. Only valid when state is "
            "CONTACTED or NEGOTIATING. State moves to DECLINED; reason is "
            "written to attributes.terminal_reason."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short reason, e.g. 'out of scope' or 'vendor withdrew'."},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "accept_quote",
        "description": (
            "Accept the vendor's recorded quote. Only valid when state is "
            "QUOTED and the context says QUOTE DECISION = accept. State "
            "moves QUOTED → SCHEDULED. Pair with a short confirmation "
            "message via send_email / send_sms / send_phone."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "decline_quote",
        "description": (
            "Decline the vendor's recorded quote. Only valid when state is "
            "QUOTED and the context says QUOTE DECISION = decline. State "
            "moves QUOTED → DECLINED. Pair with a short polite decline "
            "message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short polite reason, e.g. 'we've selected another vendor'."},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@dataclass
class ToolOutcome:
    tool_name: str
    success: bool
    message: str
    detail: Optional[dict] = None


def dispatch(
    db: Session,
    *,
    negotiation: Negotiation,
    iteration: int,
    tool_name: str,
    tool_input: dict[str, Any],
) -> ToolOutcome:
    """Execute one tool call. Returns an outcome the loop relays back as a tool_result.

    Caller (the coordinator loop) is responsible for committing.
    """
    if tool_name not in TOOL_NAMES:
        return ToolOutcome(tool_name, False, f"unknown tool: {tool_name}")

    try:
        if tool_name == "send_email":
            outcome = _send(db, negotiation, iteration, MessageChannel.EMAIL, {
                "text": tool_input.get("body", ""),
                "subject": tool_input.get("subject", ""),
            })
        elif tool_name == "send_sms":
            outcome = _send(db, negotiation, iteration, MessageChannel.SMS, {
                "text": tool_input.get("text", ""),
            })
        elif tool_name == "send_phone":
            outcome = _send(db, negotiation, iteration, MessageChannel.PHONE, {
                "text": tool_input.get("script", ""),
            })
        elif tool_name == "record_quote":
            outcome = _record_quote(negotiation, tool_input)
        elif tool_name == "record_facts":
            outcome = _record_facts(negotiation, tool_input)
        elif tool_name == "close_negotiation":
            outcome = _close_negotiation(negotiation, tool_input)
        elif tool_name == "accept_quote":
            outcome = _accept_quote(negotiation)
        elif tool_name == "decline_quote":
            outcome = _decline_quote(negotiation, tool_input)
        else:
            return ToolOutcome(tool_name, False, "unhandled")
    except Exception as e:
        logger.exception("Tool %s failed on neg %s", tool_name, negotiation.id)
        return ToolOutcome(tool_name, False, f"exception: {e}")

    # Every successful tool call gets a readiness recompute. The helper is
    # monotonic + cheap (one query), so calling it from tools that can't
    # change state (record_facts, send_*) is a harmless no-op.
    if outcome.success:
        refresh_ready_to_schedule(db, negotiation.work_order_id)
    return outcome


# ---------------------------------------------------------------------------
# Individual executors
# ---------------------------------------------------------------------------

def _send(
    db: Session,
    neg: Negotiation,
    iteration: int,
    channel: MessageChannel,
    content: dict[str, Any],
) -> ToolOutcome:
    text = str(content.get("text") or "")
    if not text.strip():
        return ToolOutcome(f"send_{channel.value}", False, "empty message body")

    # For email on the Tavi → vendor direction, also push the message into
    # MailPit so the vendor simulator can read it out of the shared bus. DB
    # remains the canonical thread regardless of whether MailPit is up.
    if channel == MessageChannel.EMAIL:
        vendor = db.get(Vendor, neg.vendor_place_id)
        if vendor and vendor.email:
            try:
                mailpit.send_tavi_to_vendor(
                    work_order_id=neg.work_order_id,
                    vendor_email=vendor.email,
                    subject=str(content.get("subject") or ""),
                    body=text,
                )
            except mailpit.MailpitUnavailable as e:
                logger.info(
                    "MailPit unavailable for Tavi→vendor send (neg=%s): %s; falling back to DB only",
                    neg.id, e,
                )

    msg = messages.append_message(
        db, neg,
        sender=MessageSender.TAVI, channel=channel, iteration=iteration,
        content=content,
    )
    return ToolOutcome(
        f"send_{channel.value}", True, "sent",
        detail={"message_id": msg.id},
    )


def _record_quote(neg: Negotiation, inp: dict[str, Any]) -> ToolOutcome:
    if neg.state != NegotiationState.NEGOTIATING:
        return ToolOutcome(
            "record_quote", False,
            f"record_quote only valid in NEGOTIATING; current state is {neg.state.value}",
        )
    try:
        price = int(inp["price_cents"])
        available = _parse_iso(inp["available_at"])
    except (KeyError, ValueError, TypeError) as e:
        return ToolOutcome("record_quote", False, f"invalid input: {e}")
    neg.quoted_price_cents = price
    neg.quoted_available_at = available
    neg.state = NegotiationState.QUOTED
    return ToolOutcome("record_quote", True, "quote recorded; state → QUOTED",
                       detail={"price_cents": price, "available_at": available.isoformat()})


def _record_facts(neg: Negotiation, inp: dict[str, Any]) -> ToolOutcome:
    facts = inp.get("facts") or {}
    if not isinstance(facts, dict):
        return ToolOutcome("record_facts", False, "`facts` must be an object")
    current = dict(neg.attributes or {})
    current.update(facts)
    neg.attributes = current
    return ToolOutcome("record_facts", True, f"merged {len(facts)} key(s)")


def _close_negotiation(neg: Negotiation, inp: dict[str, Any]) -> ToolOutcome:
    if neg.state not in (NegotiationState.CONTACTED, NegotiationState.NEGOTIATING):
        return ToolOutcome(
            "close_negotiation", False,
            f"close_negotiation only valid in CONTACTED or NEGOTIATING; current state is {neg.state.value}",
        )
    reason = str(inp.get("reason") or "unspecified")
    current = dict(neg.attributes or {})
    current["terminal_reason"] = reason
    neg.attributes = current
    neg.state = NegotiationState.DECLINED
    return ToolOutcome("close_negotiation", True, "negotiation closed; state → DECLINED")


def _accept_quote(neg: Negotiation) -> ToolOutcome:
    if neg.state != NegotiationState.QUOTED:
        return ToolOutcome(
            "accept_quote", False,
            f"accept_quote only valid in QUOTED; current state is {neg.state.value}",
        )
    neg.state = NegotiationState.SCHEDULED
    return ToolOutcome("accept_quote", True, "state → SCHEDULED")


def _decline_quote(neg: Negotiation, inp: dict[str, Any]) -> ToolOutcome:
    if neg.state != NegotiationState.QUOTED:
        return ToolOutcome(
            "decline_quote", False,
            f"decline_quote only valid in QUOTED; current state is {neg.state.value}",
        )
    reason = str(inp.get("reason") or "another vendor was selected")
    current = dict(neg.attributes or {})
    current["terminal_reason"] = reason
    neg.attributes = current
    neg.state = NegotiationState.DECLINED
    return ToolOutcome("decline_quote", True, "state → DECLINED")


def _parse_iso(s: str) -> datetime:
    # Accept either `...Z` or `...+00:00`; normalize to UTC-aware.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
