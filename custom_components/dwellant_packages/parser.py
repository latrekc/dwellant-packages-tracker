"""Pure HTML parsing helpers for the Dwellant packages table.

Kept free of Home Assistant imports so it is easy to unit-test.
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

_DELIVERY_RE = re.compile(
    r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})\s+at\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)

# Normalized type slug -> (canonical label, mdi icon hint for the card).
TYPE_MAP = {
    "a letter": ("A letter", "mdi:email-outline"),
    "package (small)": ("Package (Small)", "mdi:package-variant"),
    "package (medium)": ("Package (Medium)", "mdi:package-variant-closed"),
    "package (large)": ("Package (Large)", "mdi:package-variant-closed-plus"),
    "oversized/heavy": ("Oversized/Heavy", "mdi:forklift"),
}


def normalize_type(raw: str) -> dict:
    """Normalize a portal type label to a stable slug + label + icon."""
    key = " ".join(raw.strip().split()).lower()
    if key in TYPE_MAP:
        label, icon = TYPE_MAP[key]
        return {"slug": key, "label": label, "icon": icon}
    return {"slug": key or "unknown", "label": raw.strip(), "icon": "mdi:package"}


def parse_delivery_time(raw: str) -> dict:
    """Parse 'today, 18/09/2026 at 15:24' into ISO + raw fallback.

    Never raises: on parse failure returns {"iso": None, "raw": raw}.
    """
    text = " ".join((raw or "").split())
    match = _DELIVERY_RE.search(text)
    if not match:
        return {"iso": None, "raw": text}
    try:
        dt = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
    except ValueError:
        return {"iso": None, "raw": text}
    return {"iso": dt.isoformat(), "raw": text}


def parse_available_table(html: str) -> dict[str, dict]:
    """Parse AvailablePackageTableRows HTML into {collection_code: package}.

    Each <tr> is expected to hold 5 <td>s: unit, type, collection code,
    delivery time, concierge. Header/empty/malformed rows are skipped.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    packages: dict[str, dict] = {}
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) != 5:
            continue
        # Skip header row ("UNIT NAME | TYPE | ...").
        if cells[2].strip().lower() in ("collection code", "code", ""):
            continue
        unit, type_raw, code, delivered_raw, concierge = (
            cells[0].strip(),
            cells[1].strip(),
            cells[2].strip(),
            cells[3].strip(),
            cells[4].strip(),
        )
        if not code:
            continue
        type_info = normalize_type(type_raw)
        delivered = parse_delivery_time(delivered_raw)
        packages[code] = {
            "code": code,
            "unit": unit,
            "type": type_info["label"],
            "type_slug": type_info["slug"],
            "icon": type_info["icon"],
            "delivery_time": delivered["iso"],
            "delivery_time_raw": delivered["raw"],
            "concierge": concierge,
        }
    return packages


def extract_request_verification_token(html: str) -> str | None:
    """Extract the anti-forgery token from the login page HTML.

    Tries common ASP.NET names; returns None when not found (caller may
    proceed without a token or raise CannotConnect).
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for name in ("__RequestVerificationToken", "SOME_TOKEN", "__token"):
        tag = soup.find("input", attrs={"name": name})
        if tag and tag.get("value"):
            return tag["value"]
    # Fallback: any hidden input with 'token' or 'verification' in the name.
    for tag in soup.find_all("input", attrs={"type": "hidden"}):
        name = (tag.get("name") or "").lower()
        if "token" in name or "verification" in name:
            if tag.get("value"):
                return tag["value"]
    return None


def looks_like_login_page(html: str) -> bool:
    """Heuristic: does this HTML look like the sign-in form (expired session)?"""
    if not html:
        return False
    lowered = html.lower()
    return (
        "signinorregister" in lowered
        or "displaynameoremailaddress" in lowered
        or ("password" in lowered and "sign in" in lowered)
    )
