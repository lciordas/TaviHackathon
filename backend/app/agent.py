"""Synchronous intake agent: one LLM turn with tool-use."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from anthropic import Anthropic
from pydantic import ValidationError

from .config import settings
from .prompts import (
    PROFILE_ACK,
    SYSTEM_PROMPT_TEMPLATE,
    render_profile_message,
)
from .schemas import REQUIRED_FIELDS, ChatMessage, WorkOrderPartial
from .tools import UPDATE_FIELDS_TOOL

logger = logging.getLogger(__name__)

_client = Anthropic(api_key=settings.anthropic_api_key)

_MAX_ITERATIONS = 3


def run_turn(
    messages: list[ChatMessage],
    fields: WorkOrderPartial,
) -> tuple[str, WorkOrderPartial, bool, list[str]]:
    """Run a single agent turn.

    Returns: (reply_text, updated_fields, is_ready, missing_field_names).
    """
    known = {k: v for k, v in fields.model_dump().items() if v is not None}
    system = SYSTEM_PROMPT_TEMPLATE.format(
        known_fields_json=json.dumps(known, default=str, indent=2),
        current_date=datetime.now(timezone.utc).isoformat(),
    )

    # Prepend the hardcoded profile as a synthetic first user turn, followed
    # by an assistant ack so roles alternate before the real messages.
    api_messages: list[dict] = [
        {"role": "user", "content": render_profile_message()},
        {"role": "assistant", "content": PROFILE_ACK},
    ]
    incoming = list(messages)
    while incoming and incoming[0].role == "assistant":
        incoming.pop(0)
    for m in incoming:
        api_messages.append({"role": m.role, "content": m.content})

    accumulated = fields
    reply_text = ""

    for _ in range(_MAX_ITERATIONS):
        resp = _client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=system,
            tools=[UPDATE_FIELDS_TOOL],
            messages=api_messages,
        )

        if resp.stop_reason == "tool_use":
            api_messages.append({"role": "assistant", "content": resp.content})
            tool_results: list[dict] = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                if block.name == "update_fields":
                    try:
                        patch = WorkOrderPartial.model_validate(block.input)
                        accumulated = accumulated.merge(patch)
                    except ValidationError as exc:
                        logger.warning(
                            "update_fields rejected: %s (input=%s)",
                            exc,
                            block.input,
                        )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "ok",
                    }
                )
            api_messages.append({"role": "user", "content": tool_results})
            continue

        for block in resp.content:
            if block.type == "text":
                reply_text = block.text
                break
        break

    if not reply_text:
        reply_text = "Sorry, I missed that — could you say it again?"

    missing = [f for f in REQUIRED_FIELDS if getattr(accumulated, f) is None]
    is_ready = not missing
    return reply_text, accumulated, is_ready, missing
