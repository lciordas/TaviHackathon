"""BBB (bbb.org) scraper — politely.

There is no public BBB API. We fetch the search results page for a given
business + location, parse it for a candidate match, and follow to the profile
page to read out the letter grade, accreditation status, complaint counts,
and years in business.

Design choices:
- Fixed User-Agent string identifying the bot, no personal contact info.
- Rate-limited at module level (1 req/sec across the process) to be a polite
  citizen.
- Best-effort: returns None / null fields on missing or unparseable data.
- HTML parsing is intentionally tolerant — BBB layout shifts; we use multiple
  selectors and regex fallbacks before giving up.

Public surface:
    fetch_bbb_for_vendor(name, city, state) -> Optional[BBBProfile]
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ...config import settings


logger = logging.getLogger(__name__)


BBB_BASE = "https://www.bbb.org"
SEARCH_URL = f"{BBB_BASE}/search"

# Module-level rate-limit lock so concurrent callers don't burst BBB.
_rate_lock = threading.Lock()
_last_request_at: float = 0.0


def _polite_sleep() -> None:
    global _last_request_at
    delay = settings.bbb_request_delay_s
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)
        _last_request_at = time.monotonic()


@dataclass
class BBBProfile:
    profile_url: str
    grade: Optional[str]
    accredited: Optional[bool]
    years_accredited: Optional[int]
    complaints_total: Optional[int]
    complaints_resolved: Optional[int]
    years_in_business: Optional[int]


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=15.0,
        follow_redirects=True,
        headers={
            "User-Agent": settings.bbb_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _name_similarity(a: str, b: str) -> float:
    """Token-set-ish ratio. Lowercase, strip suffixes like LLC/Inc, compare."""
    norm = lambda s: re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    strip = lambda s: re.sub(r"\b(llc|inc|corp|co|ltd|the|services?|company)\b", " ", s)
    a2 = " ".join(strip(norm(a)).split())
    b2 = " ".join(strip(norm(b)).split())
    if not a2 or not b2:
        return 0.0
    return SequenceMatcher(None, a2, b2).ratio()


def _search_for_match(
    client: httpx.Client, name: str, city: str, state: str
) -> Optional[str]:
    """Submit a BBB search and return a profile URL for a likely match."""
    _polite_sleep()
    params = {
        "find_text": name,
        "find_loc": f"{city}, {state}",
    }
    try:
        resp = client.get(SEARCH_URL, params=params)
    except httpx.HTTPError as e:
        logger.warning("BBB search HTTP error for %r: %s", name, e)
        return None
    if resp.status_code >= 400:
        logger.info("BBB search %s for %r returned HTTP %s", params, name, resp.status_code)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    candidates: list[tuple[float, str, bool]] = []  # (similarity, url, accredited)

    for card in soup.select("div.card, article, div[data-testid*='result'], div[class*='ResultCard']"):
        link = card.find("a", href=True)
        if link is None:
            continue
        href = link["href"]
        if "/profile/" not in href:
            continue
        candidate_name = link.get_text(strip=True) or ""
        accredited = bool(card.find(string=re.compile(r"BBB\s+Accredited", re.I)))
        sim = _name_similarity(name, candidate_name)
        url = href if href.startswith("http") else BBB_BASE + href
        candidates.append((sim, url, accredited))

    if not candidates:
        # Fallback: any /profile/ link in the page
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/profile/" in href:
                url = href if href.startswith("http") else BBB_BASE + href
                candidates.append((_name_similarity(name, a.get_text(strip=True) or ""), url, False))

    candidates = [c for c in candidates if c[0] >= 0.55]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[2], c[0]), reverse=True)
    return candidates[0][1]


_GRADE_RE = re.compile(r"\b(A\+|A-|A|B\+|B-|B|C\+|C-|C|D|F|NR)\b")


def _parse_profile(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    # Grade — appears as "BBB Rating: A+" or in a dedicated grade element.
    grade: Optional[str] = None
    rating_match = re.search(r"BBB\s+Rating[:\s]*([A-F][+-]?|NR)", text, re.I)
    if rating_match:
        grade = rating_match.group(1).upper()
    else:
        for sel in ["[class*='RatingLetter']", "[class*='bds-grade']", "[data-testid*='rating']"]:
            el = soup.select_one(sel)
            if el:
                m = _GRADE_RE.search(el.get_text(" ", strip=True))
                if m:
                    grade = m.group(1).upper()
                    break

    # Accreditation
    accredited: Optional[bool] = None
    if re.search(r"BBB\s+Accredited\s+Business", text, re.I):
        accredited = True
    elif re.search(r"Not\s+BBB\s+Accredited", text, re.I):
        accredited = False

    # Years accredited — "Accredited Since: 1/1/2010" → years_accredited
    years_accredited: Optional[int] = None
    acc_since = re.search(r"Accredited\s+Since[:\s]+(\d{1,2}/\d{1,2}/(\d{4}))", text, re.I)
    if acc_since:
        try:
            year = int(acc_since.group(2))
            from datetime import datetime as _dt, timezone as _tz
            years_accredited = max(0, _dt.now(_tz.utc).year - year)
        except ValueError:
            pass

    # Complaints — patterns like "Customer Complaints: 12" "Complaints Closed in last 3 years: 12"
    complaints_total: Optional[int] = None
    complaints_resolved: Optional[int] = None
    m = re.search(r"Complaints?\s+Closed\s+in\s+last\s+3\s+Years[:\s]+(\d+)", text, re.I)
    if m:
        complaints_total = int(m.group(1))
    else:
        m = re.search(r"Customer\s+Complaints?[:\s]+(\d+)", text, re.I)
        if m:
            complaints_total = int(m.group(1))
    m = re.search(r"Complaints?\s+Resolved[:\s]+(\d+)", text, re.I)
    if m:
        complaints_resolved = int(m.group(1))

    # Years in business — "Years in Business: 14"
    years_in_business: Optional[int] = None
    m = re.search(r"Years?\s+in\s+Business[:\s]+(\d+)", text, re.I)
    if m:
        years_in_business = int(m.group(1))

    return {
        "grade": grade,
        "accredited": accredited,
        "years_accredited": years_accredited,
        "complaints_total": complaints_total,
        "complaints_resolved": complaints_resolved,
        "years_in_business": years_in_business,
    }


def fetch_profile(client: httpx.Client, profile_url: str) -> Optional[dict]:
    _polite_sleep()
    try:
        resp = client.get(profile_url)
    except httpx.HTTPError as e:
        logger.warning("BBB profile HTTP error for %s: %s", profile_url, e)
        return None
    if resp.status_code >= 400:
        logger.info("BBB profile %s -> HTTP %s", profile_url, resp.status_code)
        return None
    return _parse_profile(resp.text)


def fetch_bbb_for_vendor(name: str, city: Optional[str], state: Optional[str]) -> Optional[BBBProfile]:
    """High-level entrypoint: search → match → fetch profile → parse → return.

    Returns None when no plausible match is found (the caller should still
    record `bbb_fetched_at` on the vendor row to prevent re-scraping).
    """
    if not name or not city or not state:
        return None
    with _client() as client:
        profile_url = _search_for_match(client, name, city, state)
        if not profile_url:
            return None
        parsed = fetch_profile(client, profile_url)
        if not parsed:
            return None
        return BBBProfile(profile_url=profile_url, **parsed)
