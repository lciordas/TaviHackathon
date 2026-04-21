"""Pitch-template substitution — no LLM calls."""
from __future__ import annotations

from app.services.negotiation.pitch import PLACEHOLDER, fill


def test_fill_replaces_placeholder_in_body():
    template = {
        "subject": "Service request — plumbing",
        "body": f"Hi {PLACEHOLDER}, this is Tavi. We have a leak under the sink. Are you available Tuesday?",
    }
    out = fill(template, "Ace Plumbing & Drain")
    assert "Ace Plumbing & Drain" in out["body"]
    assert PLACEHOLDER not in out["body"]
    assert out["subject"] == "Service request — plumbing"


def test_fill_replaces_every_placeholder_occurrence():
    template = {
        "subject": "x",
        "body": f"Hi {PLACEHOLDER}, thanks. Best, Tavi — for {PLACEHOLDER}'s attention.",
    }
    out = fill(template, "Joe's Quick Fix LLC")
    assert out["body"].count("Joe's Quick Fix LLC") == 2
    assert PLACEHOLDER not in out["body"]


def test_fill_falls_back_to_prepended_greeting_if_placeholder_missing():
    """Model drift: template didn't include the placeholder. Never send
    a raw body to a vendor — prepend a greeting so the name appears."""
    template = {"subject": "x", "body": "This is Tavi. We have a plumbing job."}
    out = fill(template, "Ace Plumbing")
    assert out["body"].startswith("Hi Ace Plumbing,")
    assert "This is Tavi. We have a plumbing job." in out["body"]


def test_fill_subject_defaults_when_missing():
    template = {"body": f"Hi {PLACEHOLDER}, this is Tavi."}  # no subject
    out = fill(template, "Ace")
    assert out["subject"] == "Service request"
