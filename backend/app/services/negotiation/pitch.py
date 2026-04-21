"""Shared opening-pitch template for a work order.

Every vendor's PROSPECTING turn uses the same pitch body (with their name
substituted in). The template is generated once via Anthropic and cached on
`WorkOrder.pitch_template` so subsequent vendor turns skip the LLM.

This saves N-1 Anthropic calls per work order during the opening blast —
with N=8 vendors that's a ~87% reduction in pitch-generation cost and
latency.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from anthropic import Anthropic
from sqlalchemy.orm import Session

from ...config import settings
from ...models import WorkOrder

logger = logging.getLogger(__name__)


_client: Optional[Anthropic] = None


def _anthropic() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


PLACEHOLDER = "{{vendor_name}}"


_TEMPLATE_SYSTEM_PROMPT = f"""\
You draft a single outbound email template Tavi will send to several service
vendors about one specific work order. Tavi is an AI-native managed
marketplace that books facility service jobs (plumbing, HVAC, electrical,
landscaping, appliance repair) with qualified vendors on behalf of a
facility manager.

Produce ONE pitch that will be reused verbatim across every candidate
vendor. Use `{PLACEHOLDER}` in the body exactly where the recipient's
business name should go — a downstream string substitution fills it in.

CONTENT REQUIREMENTS
  - Greet the vendor by name using `{PLACEHOLDER}`.
  - Introduce Tavi in one short sentence.
  - Describe the job plainly from the work order (trade, one-line summary
    of what's needed, city). Do NOT invent details the work order doesn't
    provide.
  - State the requested date/window.
  - Ask whether the vendor is interested and available.
  - Close professionally — no sign-off with a real person's name.

STYLE
  - Plain prose. No markdown, no bullet lists, no headers.
  - 3–5 short sentences in the body.
  - Professional, not stiff.
  - Subject line: short and job-focused ("Service request — plumbing"
    or similar).

OUTPUT
Emit the template via the `emit_pitch_template` tool call. Do not write
anything outside the tool.
"""


_TEMPLATE_TOOL: dict[str, Any] = {
    "name": "emit_pitch_template",
    "description": "Emit the single reusable pitch template for this work order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "Short, job-focused subject line.",
            },
            "body": {
                "type": "string",
                "description": (
                    f"Email body using `{PLACEHOLDER}` exactly where the "
                    "recipient's business name should be substituted."
                ),
            },
        },
        "required": ["subject", "body"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

def get_or_generate(db: Session, work_order: WorkOrder) -> dict[str, str]:
    """Return the cached template, generating + persisting one if needed.

    Returns `{"subject": str, "body": str}`. The body contains the
    `{{vendor_name}}` placeholder.
    """
    if work_order.pitch_template:
        try:
            data = json.loads(work_order.pitch_template)
            if isinstance(data, dict) and "subject" in data and "body" in data:
                return {"subject": str(data["subject"]), "body": str(data["body"])}
        except json.JSONDecodeError:
            logger.warning("Invalid pitch_template JSON on wo=%s; regenerating", work_order.id)

    template = _generate(work_order)
    work_order.pitch_template = json.dumps(template)
    db.flush()
    return template


def fill(template: dict[str, str], vendor_name: str) -> dict[str, str]:
    """Substitute the vendor name into the template body.

    If the template didn't include `{{vendor_name}}` (model drift), fall
    back to prepending a greeting — we never send a pitch with a literal
    placeholder to a real vendor.
    """
    body = template.get("body", "")
    if PLACEHOLDER in body:
        body = body.replace(PLACEHOLDER, vendor_name)
    else:
        body = f"Hi {vendor_name},\n\n{body}"
    subject = template.get("subject") or "Service request"
    return {"subject": subject, "body": body}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _generate(work_order: WorkOrder) -> dict[str, str]:
    context = _render_context(work_order)

    resp = _anthropic().messages.create(
        model=settings.anthropic_model,
        max_tokens=512,
        system=_TEMPLATE_SYSTEM_PROMPT,
        tools=[_TEMPLATE_TOOL],
        tool_choice={"type": "tool", "name": "emit_pitch_template"},
        messages=[{"role": "user", "content": context}],
    )

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_pitch_template":
            data = dict(block.input or {})
            subject = str(data.get("subject") or "").strip() or _fallback_subject(work_order)
            body = str(data.get("body") or "").strip() or _fallback_body(work_order)
            return {"subject": subject, "body": body}

    # The tool_choice should guarantee a tool_use block. Log + fall back if not.
    logger.warning("Pitch-template generation returned no tool_use block for wo=%s", work_order.id)
    return {"subject": _fallback_subject(work_order), "body": _fallback_body(work_order)}


def _render_context(work_order: WorkOrder) -> str:
    lines = [
        "Work order details:",
        f"  trade: {work_order.trade.value}",
        f"  description: {work_order.description}",
        f"  location: {work_order.city}, {work_order.state}",
        f"  requested_for: {work_order.scheduled_for.isoformat()}",
        f"  urgency: {work_order.urgency.value}",
    ]
    if work_order.access_notes:
        lines.append(f"  access_notes: {work_order.access_notes}")
    lines.append("")
    lines.append(
        f"Draft the pitch template. Remember to use `{PLACEHOLDER}` where "
        "the recipient's name goes."
    )
    return "\n".join(lines)


def _fallback_subject(work_order: WorkOrder) -> str:
    return f"Service request — {work_order.trade.value}"


def _fallback_body(work_order: WorkOrder) -> str:
    return (
        f"Hi {PLACEHOLDER},\n\nThis is Tavi. We have a {work_order.trade.value} "
        f"job in {work_order.city} that needs attention around "
        f"{work_order.scheduled_for.strftime('%b %d')}. Are you available?"
    )
