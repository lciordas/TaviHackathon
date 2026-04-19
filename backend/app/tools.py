"""Anthropic tool definitions for the intake agent."""

UPDATE_FIELDS_TOOL = {
    "name": "update_fields",
    "description": (
        "Record one or more work-order fields you just learned from the user "
        "or derived from their profile. Call this whenever you learn or "
        "confirm a field. Only include fields you are confident about — "
        "omit the rest. Each call OVERWRITES the listed fields; existing "
        "values for unmentioned fields are preserved. Do NOT call this tool "
        "just to ask a question; emit a text reply for that."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "trade": {
                "type": "string",
                "enum": [
                    "plumbing",
                    "hvac",
                    "electrical",
                    "lawncare",
                    "handyman",
                    "appliance_repair",
                ],
                "description": (
                    "The trade category. Map user wording to one of these "
                    "(e.g. 'AC' -> 'hvac', 'lights' -> 'electrical')."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "1-3 sentences summarizing what needs doing, in your "
                    "own words. Factual, no fluff."
                ),
            },
            "access_notes": {
                "type": "string",
                "description": (
                    "Site access notes: on-site contact, parking, entry "
                    "hours. Use an empty string if the user explicitly said "
                    "there are none."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": ["emergency", "urgent", "scheduled", "flexible"],
                "description": (
                    "emergency = same-day, active damage. "
                    "urgent = within 24h. "
                    "scheduled = specific future date. "
                    "flexible = whenever."
                ),
            },
            "scheduled_for": {
                "type": "string",
                "format": "date-time",
                "description": (
                    "Desired service start time in ISO 8601 UTC. Resolve "
                    "relative dates ('next Tuesday afternoon') using the "
                    "'Today's date' field injected in the system prompt."
                ),
            },
            "budget_cap_cents": {
                "type": "integer",
                "description": "Hard upper bound on spend, in cents. $1,500 = 150000.",
            },
            "quality_threshold": {
                "type": "number",
                "description": (
                    "Minimum vendor composite score (0-5 scale). Infer "
                    "from cues ('top-tier' ~ 4.5, 'solid' ~ 4.0, 'doesn't "
                    "matter' ~ 3.0) or seed from the user profile default."
                ),
            },
            "requires_licensed": {"type": "boolean"},
            "requires_insured": {
                "type": "boolean",
                "description": "Default true for any commercial job unless the user says otherwise.",
            },
        },
        "additionalProperties": False,
    },
}
