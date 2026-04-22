"""MailPit client — SMTP out + HTTP search/read in.

MailPit runs locally (default ports: SMTP 1025, HTTP 8025). Tavi and the
simulated vendors all route mail through it:

  - Tavi outbound (coordinator): SMTP send From=tavi+{wo_id}@tavi.local
    To={vendor.email}. Plus-addressing makes the inbound sweep trivial
    since every reply naturally lands at tavi+{wo_id}@...
  - Vendor outbound (simulator): SMTP send From={vendor.email}
    To=tavi+{wo_id}@tavi.local.
  - Simulator's thread read: HTTP search over {to,from}:{vendor.email}
    to rebuild the full conversation in vendor perspective — the
    simulator never touches the DB.
  - Inbound sweep (scheduler, end of tick): HTTP search for unread
    messages to tavi+{wo_id}@... From anyone; write each to
    negotiation_messages and mark read.

All functions raise `MailpitUnavailable` on network/service errors so
callers can fall back to direct DB writes. MailPit is advisory — the DB
remains the canonical thread — but when it's up, it's the only real path
between Tavi and the vendors.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Optional

import httpx

from ...config import settings

logger = logging.getLogger(__name__)


class MailpitUnavailable(RuntimeError):
    """Raised when MailPit is down or returns an unexpected error.

    Callers should catch and fall back to the DB-only path.
    """


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def tavi_address(work_order_id: str) -> str:
    """Plus-addressed inbox for one work order: tavi+{wo_id}@{domain}."""
    return f"tavi+{work_order_id}@{settings.tavi_email_domain}"


def is_tavi_address(addr: Optional[str]) -> bool:
    if not addr:
        return False
    return addr.startswith("tavi+") and addr.endswith(f"@{settings.tavi_email_domain}")


# ---------------------------------------------------------------------------
# Email records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmailRecord:
    id: str               # MailPit message id
    from_addr: str
    to_addr: str
    subject: str
    text: str
    read: bool


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------

def send_tavi_to_vendor(
    *,
    work_order_id: str,
    vendor_email: str,
    subject: str,
    body: str,
) -> None:
    """SMTP-send a Tavi → vendor message through MailPit."""
    _smtp_send(
        from_addr=tavi_address(work_order_id),
        to_addr=vendor_email,
        subject=subject,
        body=body,
    )


def send_vendor_to_tavi(
    *,
    work_order_id: str,
    vendor_email: str,
    subject: str,
    body: str,
) -> None:
    """SMTP-send a vendor → Tavi reply through MailPit."""
    _smtp_send(
        from_addr=vendor_email,
        to_addr=tavi_address(work_order_id),
        subject=subject,
        body=body,
    )


def _smtp_send(*, from_addr: str, to_addr: str, subject: str, body: str) -> None:
    if not settings.mailpit_enabled:
        raise MailpitUnavailable("MailPit disabled by config")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")

    try:
        with smtplib.SMTP(settings.mailpit_smtp_host, settings.mailpit_smtp_port, timeout=3.0) as s:
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailpitUnavailable(f"SMTP send failed: {e}") from e


# ---------------------------------------------------------------------------
# Inbound — search + fetch + mark read
# ---------------------------------------------------------------------------

def _api_client() -> httpx.Client:
    return httpx.Client(base_url=settings.mailpit_api_base, timeout=3.0)


def fetch_vendor_thread(vendor_email: str) -> list[EmailRecord]:
    """Full conversation (both directions) for this vendor's address.

    Uses MailPit's `addressed:` filter which matches to/from/cc/bcc in
    one query — MailPit's search language doesn't support parenthesized
    OR expressions. Messages are returned oldest-first so they map
    directly to Anthropic's messages[] ordering.
    """
    query = f'addressed:"{vendor_email}"'
    return _search(query)


def fetch_unread_for_tavi(work_order_id: str) -> list[EmailRecord]:
    """Vendor replies to Tavi for this work order that we haven't yet
    pulled into the DB. Returned oldest-first."""
    tavi = tavi_address(work_order_id)
    query = f'to:"{tavi}" is:unread'
    return _search(query)


def mark_read(message_id: str) -> None:
    """Flip a message's read flag. Best-effort: we swallow failures here
    since the consequence of a miss is just re-processing next tick."""
    try:
        with _api_client() as client:
            resp = client.put("/api/v1/messages", json={"ids": [message_id], "read": True})
        if resp.status_code >= 400:
            logger.warning("MailPit mark_read %s → %s %s", message_id, resp.status_code, resp.text[:200])
    except httpx.HTTPError as e:
        logger.warning("MailPit mark_read %s failed: %s", message_id, e)


def _search(query: str) -> list[EmailRecord]:
    if not settings.mailpit_enabled:
        raise MailpitUnavailable("MailPit disabled by config")

    try:
        with _api_client() as client:
            resp = client.get("/api/v1/search", params={"query": query, "limit": 200})
            if resp.status_code >= 400:
                raise MailpitUnavailable(f"search {resp.status_code}: {resp.text[:200]}")
            data = resp.json()

            out: list[EmailRecord] = []
            # MailPit returns newest-first; reverse for chronological order.
            for m in reversed(data.get("messages", [])):
                msg_id = str(m.get("ID") or "")
                if not msg_id:
                    continue
                from_addr = _addr(m.get("From"))
                to_list = m.get("To") or []
                to_addr = _addr(to_list[0]) if to_list else ""
                subject = str(m.get("Subject") or "")
                read = bool(m.get("Read", False))
                text = _fetch_body(client, msg_id)
                out.append(EmailRecord(
                    id=msg_id,
                    from_addr=from_addr,
                    to_addr=to_addr,
                    subject=subject,
                    text=text,
                    read=read,
                ))
            return out
    except httpx.HTTPError as e:
        raise MailpitUnavailable(f"MailPit HTTP error: {e}") from e


def _fetch_body(client: httpx.Client, message_id: str) -> str:
    """Pull the plain-text body for one message (search endpoint doesn't
    include bodies)."""
    resp = client.get(f"/api/v1/message/{message_id}")
    if resp.status_code >= 400:
        logger.warning("MailPit fetch_body %s → %s", message_id, resp.status_code)
        return ""
    return str(resp.json().get("Text") or "")


def _addr(obj) -> str:
    """MailPit's search result encodes addresses as `{"Name":"","Address":"x@y"}`
    or sometimes as raw strings. Normalize to the bare email."""
    if obj is None:
        return ""
    if isinstance(obj, dict):
        return str(obj.get("Address") or "")
    if isinstance(obj, str):
        _name, email = parseaddr(obj)
        return email or obj
    return ""


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health() -> bool:
    """Quick liveness probe for MailPit. Returns True if the HTTP API is
    reachable; False otherwise. Used for diagnostics / logging only."""
    if not settings.mailpit_enabled:
        return False
    try:
        with _api_client() as client:
            resp = client.get("/api/v1/info", timeout=1.0)
            return resp.status_code < 400
    except httpx.HTTPError:
        return False
