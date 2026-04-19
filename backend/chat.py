"""Interactive REPL for chatting with the Tavi intake agent.

Start the server first:
    uv run uvicorn app.main:app --reload --port 8000

Then run this REPL:
    uv run python chat.py

Commands:
    /quit    exit the REPL
    /fields  print the currently-known fields
    /submit  explicitly call /intake/confirm (also triggered automatically
             by a short affirmative reply once all fields are collected)
"""

import json
import sys

import requests

BASE = "http://localhost:8000"

# Short phrases that count as "ok, submit" once all fields are collected.
_AFFIRMATIVE = {
    "y", "yes", "yeah", "yep", "yup", "ya",
    "ok", "okay", "k",
    "sure", "confirm", "confirmed", "submit",
    "do it", "go", "go ahead", "send", "send it", "ship it",
    "looks good", "lgtm", "sounds good", "good",
    "correct", "right", "that's right", "all good",
    "affirmative", "perfect", "great", "fine",
    "absolutely", "for sure", "please do", "please",
}


def _fmt_fields(fields: dict) -> str:
    non_null = {k: v for k, v in fields.items() if v is not None}
    return json.dumps(non_null, indent=2, default=str) if non_null else "{}"


def _is_affirmative(text: str) -> bool:
    norm = text.lower().strip().rstrip(".!?,").strip()
    if len(norm) > 30:
        return False
    return norm in _AFFIRMATIVE


def _submit(fields: dict) -> bool:
    """Call /intake/confirm. Returns True if the row was created."""
    try:
        r = requests.post(f"{BASE}/intake/confirm", json={"fields": fields}, timeout=30)
    except requests.RequestException as exc:
        print(f"Submit error: {exc}\n")
        return False

    if r.status_code == 400:
        print(f"\nCan't submit yet: {r.json()}\n")
        return False
    r.raise_for_status()
    out = r.json()
    print(f"\nSubmitted. Work order ID: {out['id']}\n")
    return True


def main() -> None:
    try:
        started = requests.post(f"{BASE}/intake/start", timeout=30).json()
    except requests.RequestException as exc:
        print(f"Can't reach {BASE}: {exc}")
        print("Is the server running? `uv run uvicorn app.main:app --reload --port 8000`")
        sys.exit(1)

    messages: list[dict] = [{"role": "assistant", "content": started["greeting"]}]
    fields: dict = started["fields"]
    is_ready: bool = False

    print("\n--- Tavi intake chat  (/quit  /fields  /submit) ---\n")
    print(f"Agent: {started['greeting']}\n")

    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not user:
            continue
        if user == "/quit":
            return
        if user == "/fields":
            print(f"Current fields:\n{_fmt_fields(fields)}\n")
            continue
        if user == "/submit":
            if _submit(fields):
                return
            continue

        # Auto-submit: once is_ready=true, a short affirmative counts as confirmation.
        if is_ready and _is_affirmative(user):
            if _submit(fields):
                return
            continue

        messages.append({"role": "user", "content": user})
        try:
            resp = requests.post(
                f"{BASE}/intake/chat",
                json={"messages": messages, "fields": fields},
                timeout=60,
            ).json()
        except requests.RequestException as exc:
            print(f"Chat error: {exc}\n")
            messages.pop()
            continue

        messages.append({"role": "assistant", "content": resp["reply"]})
        fields = resp["fields"]
        is_ready = resp["is_ready"]

        print(f"\nAgent: {resp['reply']}\n")


if __name__ == "__main__":
    main()
