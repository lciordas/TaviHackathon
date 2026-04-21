"""Message-thread helpers for the negotiation subsystem.

Centralizes:
  - appending a message row (coordinator outbound or vendor reply) and
    applying the state side-effects that the spec attaches to the insert
    (PROSPECTING → CONTACTED on first Tavi send; CONTACTED → NEGOTIATING on
    first vendor reply).
  - reading thread history in the perspective each agent needs: the
    coordinator sees Tavi messages as its own (assistant) and vendor
    messages as "user"; the vendor simulator sees it flipped.

Keeping all three in one module means the state-machine side-effects live
in exactly one place.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ...enums import MessageChannel, MessageSender, NegotiationState
from ...models import Negotiation, NegotiationMessage


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def append_message(
    db: Session,
    negotiation: Negotiation,
    *,
    sender: MessageSender,
    channel: MessageChannel,
    iteration: int,
    content: dict[str, Any],
) -> NegotiationMessage:
    """Append a message and apply any state side-effects the insert triggers.

    Two implicit transitions (per `Step 3.md` → *State transitions*):
      - TAVI message while state == PROSPECTING → state = CONTACTED
      - VENDOR message while state == CONTACTED → state = NEGOTIATING

    Quote-related transitions (NEGOTIATING → QUOTED, QUOTED → SCHEDULED, etc.)
    are NOT side-effects of a message — they happen via explicit coordinator
    tool calls and the caller sets `negotiation.state` accordingly.

    Caller is responsible for committing.
    """
    msg = NegotiationMessage(
        negotiation_id=negotiation.id,
        sender=sender,
        channel=channel,
        iteration=iteration,
        content=content,
    )
    db.add(msg)

    if sender == MessageSender.TAVI and negotiation.state == NegotiationState.PROSPECTING:
        negotiation.state = NegotiationState.CONTACTED
    elif sender == MessageSender.VENDOR and negotiation.state == NegotiationState.CONTACTED:
        negotiation.state = NegotiationState.NEGOTIATING

    db.flush()
    return msg


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def messages_for(db: Session, negotiation_id: str) -> list[NegotiationMessage]:
    """Full thread for one negotiation, oldest first."""
    return (
        db.query(NegotiationMessage)
        .filter(NegotiationMessage.negotiation_id == negotiation_id)
        .order_by(NegotiationMessage.iteration.asc(), NegotiationMessage.created_at.asc())
        .all()
    )


def last_message(db: Session, negotiation_id: str) -> Optional[NegotiationMessage]:
    """Most recent message on this thread, or None if empty."""
    return (
        db.query(NegotiationMessage)
        .filter(NegotiationMessage.negotiation_id == negotiation_id)
        .order_by(NegotiationMessage.iteration.desc(), NegotiationMessage.created_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Perspective-flipped renders for the two LLMs
# ---------------------------------------------------------------------------

def _content_as_text(content: dict[str, Any]) -> str:
    """Flatten the JSON `content` blob to a plain-text rendering for prompts.

    Email messages carry a subject; everything else is just the body. We
    render email as `Subject: ...\\n\\n...` so the LLM sees the same shape a
    recipient would.
    """
    text = str(content.get("text") or "")
    subject = content.get("subject")
    if subject:
        return f"Subject: {subject}\n\n{text}"
    return text


def thread_for_coordinator(db: Session, negotiation_id: str) -> list[dict[str, Any]]:
    """Return the thread as Anthropic messages[] from the coordinator's POV.

    Coordinator is the assistant (it's "us"); vendor replies show up as
    `user` so they feel like incoming turns. Channel is preserved in a
    leading tag so the model can match register in its reply.
    """
    out: list[dict[str, Any]] = []
    for m in messages_for(db, negotiation_id):
        role = "assistant" if m.sender == MessageSender.TAVI else "user"
        tag = f"[{m.channel.value}] "
        out.append({"role": role, "content": tag + _content_as_text(m.content)})
    return out


def thread_for_simulator(db: Session, negotiation_id: str) -> list[dict[str, Any]]:
    """Return the thread as Anthropic messages[] from the vendor's POV.

    Flipped: vendor messages are the assistant, Tavi messages are incoming
    `user` turns. Tool-use blocks from the coordinator (record_quote,
    record_facts, etc.) are NOT represented here — those are private to
    Tavi and never reach the vendor.
    """
    out: list[dict[str, Any]] = []
    for m in messages_for(db, negotiation_id):
        role = "assistant" if m.sender == MessageSender.VENDOR else "user"
        tag = f"[{m.channel.value}] "
        out.append({"role": role, "content": tag + _content_as_text(m.content)})
    return out
