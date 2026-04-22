"""Validate Anthropic + Google Places credentials before running the app.

Run via `make doctor` from the repo root, or `uv run python doctor.py` from
the backend directory. Exits 0 if both providers authenticate and the
configured Anthropic model is accessible; exits 1 otherwise.
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv


def _fmt_err(msg: str, max_len: int = 240) -> str:
    msg = msg.strip().replace("\n", " ")
    return msg if len(msg) <= max_len else msg[:max_len] + "..."


def check_anthropic() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()

    if not key or key.startswith("sk-ant-..."):
        print("  ✗ Anthropic: ANTHROPIC_API_KEY not set in backend/.env (still placeholder)")
        return False

    try:
        import anthropic
    except ImportError:
        print("  ✗ Anthropic: anthropic SDK not installed — run 'make setup'")
        return False

    try:
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
    except anthropic.AuthenticationError:
        print("  ✗ Anthropic: authentication failed — key is invalid or revoked")
        return False
    except anthropic.NotFoundError as e:
        print(f"  ✗ Anthropic: model '{model}' not accessible on this key")
        print(f"    fix: set ANTHROPIC_MODEL in backend/.env to a model your key supports")
        print(f"         (e.g. claude-sonnet-4-5, or request Sonnet 4.6 access in the Anthropic console)")
        return False
    except anthropic.PermissionDeniedError as e:
        print(f"  ✗ Anthropic: permission denied — {_fmt_err(str(e))}")
        return False
    except anthropic.BadRequestError as e:
        msg = str(e).lower()
        if "credit" in msg or "billing" in msg:
            print("  ✗ Anthropic: billing problem — add prepaid credits at console.anthropic.com")
        else:
            print(f"  ✗ Anthropic: bad request — {_fmt_err(str(e))}")
        return False
    except Exception as e:
        print(f"  ✗ Anthropic: {type(e).__name__} — {_fmt_err(str(e))}")
        return False

    print(f"  ✓ Anthropic: {model} reachable and authenticated")
    return True


def check_google_places() -> bool:
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()

    if not key:
        print("  ✗ Google Places: GOOGLE_PLACES_API_KEY not set in backend/.env")
        return False

    try:
        resp = httpx.post(
            "https://places.googleapis.com/v1/places:searchText",
            json={"textQuery": "plumber in Austin TX", "maxResultCount": 1},
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.id",
            },
            timeout=10.0,
        )
    except httpx.RequestError as e:
        print(f"  ✗ Google Places: network error — {_fmt_err(str(e))}")
        return False

    if resp.status_code == 200:
        count = len(resp.json().get("places", []))
        print(f"  ✓ Google Places: searchText OK ({count} result{'s' if count != 1 else ''})")
        return True

    try:
        err = resp.json().get("error", {})
        reason = err.get("message", "")
        status_code = err.get("status", "")
    except Exception:
        reason = resp.text
        status_code = ""

    reason_lower = reason.lower()

    if resp.status_code == 403:
        if "SERVICE_DISABLED" in status_code or "has not been used" in reason_lower or "places api" in reason_lower:
            print("  ✗ Google Places: API not enabled on this GCP project")
            print("    fix: enable 'Places API (New)' at console.cloud.google.com/apis/library")
            print("         note: it must be 'Places API (New)', NOT the legacy 'Places API'")
        elif "billing" in reason_lower:
            print("  ✗ Google Places: billing not enabled on the GCP project")
            print("    fix: link a billing account at console.cloud.google.com/billing")
        elif "referer" in reason_lower or "http referer" in reason_lower:
            print("  ✗ Google Places: key has HTTP referrer restrictions blocking local dev")
            print("    fix: remove restrictions for local dev in Cloud Console → Credentials")
        elif "ip" in reason_lower and "restrict" in reason_lower:
            print("  ✗ Google Places: key has IP restrictions blocking this host")
            print("    fix: remove restrictions for local dev in Cloud Console → Credentials")
        else:
            print(f"  ✗ Google Places: 403 — {_fmt_err(reason)}")
    elif resp.status_code == 400:
        print(f"  ✗ Google Places: 400 bad request — {_fmt_err(reason)}")
    elif resp.status_code == 429:
        print("  ✗ Google Places: rate limited / quota exceeded")
    else:
        print(f"  ✗ Google Places: HTTP {resp.status_code} — {_fmt_err(reason)}")
    return False


def main() -> int:
    load_dotenv()
    print("checking API credentials...")
    print()
    ok_a = check_anthropic()
    ok_g = check_google_places()
    print()
    if ok_a and ok_g:
        print("all checks passed. run 'make dev' to start the app.")
        return 0
    print("one or more checks failed. fix the issues above and re-run 'make doctor'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
