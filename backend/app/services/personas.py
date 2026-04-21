"""Persona pool + assignment for simulated vendors.

A fixed pool of markdown persona files lives in `backend/app/personas/pool/`.
At vendor-first-cache time, `assign_to_vendor` picks one at random and writes
its full content into `Vendor.persona_markdown`, plus synthesizes a contact
email so the email-first channel preference always exercises in the demo.

The pool is read once at import time and cached; adding a persona requires
a process restart.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..models import Vendor

logger = logging.getLogger(__name__)


POOL_DIR = Path(__file__).resolve().parent.parent / "personas" / "pool"


@dataclass(frozen=True)
class Persona:
    name: str            # filename stem, e.g. "03_precision_terse"
    markdown: str        # full file content
    responsiveness: str  # parsed trait: "prompt" | "terse" | "slow"


def _parse_responsiveness(markdown: str) -> str:
    """Pull the `responsiveness` trait from a persona markdown file.

    Looks for a line like `- responsiveness: slow`. Falls back to `prompt`
    if the trait is missing so new persona files don't stall the scheduler.
    """
    for line in markdown.splitlines():
        m = re.match(r"\s*-\s*responsiveness\s*:\s*([a-zA-Z_-]+)\s*$", line)
        if m:
            val = m.group(1).lower()
            if val in {"prompt", "terse", "slow"}:
                return val
            logger.warning("Unknown responsiveness value %r, defaulting to prompt", val)
            return "prompt"
    return "prompt"


def _load_pool() -> list[Persona]:
    if not POOL_DIR.exists():
        logger.warning("Persona pool directory missing: %s", POOL_DIR)
        return []
    out: list[Persona] = []
    for p in sorted(POOL_DIR.glob("*.md")):
        md = p.read_text(encoding="utf-8")
        out.append(Persona(name=p.stem, markdown=md, responsiveness=_parse_responsiveness(md)))
    if not out:
        logger.warning("Persona pool is empty")
    return out


POOL: list[Persona] = _load_pool()


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def pick_random() -> Optional[Persona]:
    """Return one persona drawn uniformly at random. None if the pool is empty."""
    if not POOL:
        return None
    return random.choice(POOL)


# Skip probability per responsiveness class. Vendors "waste" ticks before
# replying at roughly these rates — this is what gives `slow` personas a
# visibly cold feel in the UI, where `negotiation_messages.iteration` shows
# a gap since the last Tavi message.
SKIP_PROBABILITY: dict[str, float] = {
    "prompt": 0.10,
    "terse": 0.20,
    "slow": 0.60,
}


def skip_probability_for(markdown: Optional[str]) -> float:
    """How likely this persona is to sit out the current tick. Defaults to
    `prompt`-level if no persona is assigned (shouldn't happen in the live
    path, but keeps the scheduler robust)."""
    if not markdown:
        return SKIP_PROBABILITY["prompt"]
    return SKIP_PROBABILITY.get(_parse_responsiveness(markdown), SKIP_PROBABILITY["prompt"])


# ---------------------------------------------------------------------------
# Vendor assignment
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _email_slug(display_name: str) -> str:
    slug = _SLUG_RE.sub("-", display_name.lower()).strip("-")
    return slug or "vendor"


def synthesize_email(display_name: str) -> str:
    """Fake but deterministic contact address for demo purposes."""
    return f"contact@{_email_slug(display_name)}.example"


def assign_to_vendor(vendor: Vendor) -> None:
    """Attach a persona + synthesized email to a freshly-created Vendor row.

    Idempotent: if either field is already set, it's left alone — re-running
    discovery won't shuffle personalities out from under an in-flight
    negotiation.
    """
    if vendor.persona_markdown is None:
        persona = pick_random()
        if persona is not None:
            vendor.persona_markdown = persona.markdown
    if vendor.email is None:
        vendor.email = synthesize_email(vendor.display_name or "vendor")
