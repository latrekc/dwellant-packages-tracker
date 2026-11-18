"""Tests for gmail.py IMAP enrichment (fail-open, server-side SEARCH)."""

import asyncio
import sys
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.dwellant_packages.gmail import (  # noqa: E402
    ImapEnrichmentClient,
    _decode_body,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_email(body_html: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Package received R4G3D6"
    msg["From"] = "concierge@example.com"
    msg.set_content("plain fallback line")
    msg.add_alternative(body_html, subtype="html")
    return msg.as_bytes()


def test_extract_gmail_description_last_paragraph():
    html = (FIXTURES / "gmail_email.html").read_text()
    msg = EmailMessage()
    msg.set_content(html, subtype="html")
    assert _decode_body(msg) is not None
    from custom_components.dwellant_packages.parser import extract_gmail_description

    assert extract_gmail_description(_decode_body(msg)) == "A Miele package - DPD"


def test_decode_body_prefers_html():
    msg = EmailMessage()
    msg.set_content("plain text")
    msg.add_alternative("<html><body><p>html part</p></body></html>", subtype="html")
    assert _decode_body(msg).strip() == "<html><body><p>html part</p></body></html>"


def test_client_disabled_without_password():
    client = ImapEnrichmentClient("user@example.com", {})
    assert client.enabled is False
    result = asyncio.run(client.async_fetch_descriptions(["ABC123"]))
    assert result == {"ABC123": {"email_found": False, "description": None}}


def test_client_searches_server_side_and_extracts():
    html = (FIXTURES / "gmail_email.html").read_text()
    raw = _make_email(html)

    fake_imap = MagicMock()
    fake_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    fake_imap.select.return_value = ("OK", [b"100"])
    fake_imap.uid.side_effect = [
        ("OK", [b"10 11"]),  # TEXT SEARCH -> two UIDs
        ("OK", [b""]),  # X-GM-RAW diagnostic -> empty
        ("OK", [b""]),  # X-GM-RAW in:anywhere diagnostic -> empty
        ("OK", [(b"11 (BODY[] {...}", raw)]),  # FETCH newest -> match
    ]

    with patch(
        "custom_components.dwellant_packages.gmail.imaplib.IMAP4_SSL",
        return_value=fake_imap,
    ):
        client = ImapEnrichmentClient(
            "user@example.com",
            {"imap_host": "imap.gmail.com", "imap_password": "app-pass"},
        )
        result = asyncio.run(client.async_fetch_descriptions(["R4G3D6"]))

    assert result == {
        "R4G3D6": {"email_found": True, "description": "A Miele package - DPD"}
    }
    # Server-side SEARCH carried the code — no bulk fetch.
    text_search = next(
        c for c in fake_imap.uid.call_args_list if c.args[0] == "SEARCH" and c.args[1] == "TEXT"
    )
    assert "R4G3D6" in text_search.args[2]


def test_client_fail_open_on_login_error():
    fake_imap = MagicMock()
    fake_imap.login.side_effect = Exception("bad credentials")

    with patch(
        "custom_components.dwellant_packages.gmail.imaplib.IMAP4_SSL",
        return_value=fake_imap,
    ):
        client = ImapEnrichmentClient(
            "user@example.com", {"imap_password": "wrong"}
        )
        result = asyncio.run(client.async_fetch_descriptions(["XYZ"]))

    assert result == {"XYZ": {"email_found": False, "description": None}}


def test_client_flags_email_found_even_when_note_empty():
    """An email containing the code but no note -> found, description None."""
    msg = EmailMessage()
    msg["Subject"] = "Package received EMPTY01"
    msg.set_content("plain fallback")
    msg.add_alternative(
        "<html><body><div>reference code EMPTY01</div></body></html>",
        subtype="html",
    )
    raw = msg.as_bytes()

    fake_imap = MagicMock()
    fake_imap.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    fake_imap.select.return_value = ("OK", [b"100"])
    fake_imap.uid.side_effect = [
        ("OK", [b"7"]),  # TEXT SEARCH -> one UID
        ("OK", [b""]),  # X-GM-RAW diagnostic -> empty
        ("OK", [b""]),  # X-GM-RAW in:anywhere diagnostic -> empty
        ("OK", [(b"7 (BODY[] {...}", raw)]),  # FETCH -> body has code, empty note
    ]

    with patch(
        "custom_components.dwellant_packages.gmail.imaplib.IMAP4_SSL",
        return_value=fake_imap,
    ):
        client = ImapEnrichmentClient(
            "user@example.com", {"imap_password": "app-pass"}
        )
        result = asyncio.run(client.async_fetch_descriptions(["EMPTY01"]))

    assert result == {"EMPTY01": {"email_found": True, "description": None}}
