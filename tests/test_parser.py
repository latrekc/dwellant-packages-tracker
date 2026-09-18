"""Tests for parser.py (pure HTML parsing, no HA needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.dwellant_packages.parser import (  # noqa: E402
    extract_request_verification_token,
    looks_like_login_page,
    normalize_type,
    parse_available_table,
    parse_delivery_time,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_table_rows():
    html = (FIXTURES / "table_rows.html").read_text()
    packages = parse_available_table(html)
    assert set(packages) == {"T3ST01", "T3ST02"}
    med = packages["T3ST01"]
    assert med["unit"] == "12 Example House"
    assert med["type"] == "Package (Medium)"
    assert med["delivery_time"] == "2026-09-18T15:24:00"
    assert med["delivery_time_raw"] == "today, 18/09/2026 at 15:24"
    assert med["concierge"] == "Front Desk"
    assert packages["T3ST02"]["type"] == "Package (Small)"


def test_parse_table_skips_header_and_empty():
    html = (
        "<tr><th>UNIT NAME</th><th>TYPE</th><th>COLLECTION CODE</th>"
        "<th>DELIVERY TIME</th><th>DELIVERY CONCIERGE</th></tr>"
        "<tr><td>a</td><td>b</td></tr>"
        "<tr><td>U</td><td>A letter</td><td></td><td>t</td><td>c</td></tr>"
    )
    assert parse_available_table(html) == {}


def test_parse_delivery_time_variants():
    assert parse_delivery_time("today, 18/09/2026 at 15:24")["iso"] == "2026-09-18T15:24:00"
    assert parse_delivery_time("yesterday, 17/09/2026 at 09:05")["iso"] == "2026-09-17T09:05:00"
    bad = parse_delivery_time("sometime soon")
    assert bad["iso"] is None and bad["raw"] == "sometime soon"
    assert parse_delivery_time("") == {"iso": None, "raw": ""}


def test_normalize_types():
    assert normalize_type("A letter")["slug"] == "a letter"
    assert normalize_type("Package (Large)")["label"] == "Package (Large)"
    assert normalize_type("Oversized/Heavy")["icon"] == "mdi:forklift"
    assert normalize_type("Weird Thing")["icon"] == "mdi:package"


def test_token_and_login_detection():
    html = (FIXTURES / "login_page.html").read_text()
    assert extract_request_verification_token(html) == "FAKE-TOKEN-123"
    assert looks_like_login_page(html) is True
    assert looks_like_login_page("<tr><td>T3ST01</td></tr>") is False
