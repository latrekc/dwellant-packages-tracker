"""Tests for coordinator IMAP re-search semantics (no live hass needed).

Covers: HA restart re-searches ALL codes (available + collected history),
codes flagged email_found are skipped within a run, unflagged collected
codes are re-searched so late-arriving emails are picked up.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.dwellant_packages.coordinator import DwellantCoordinator  # noqa: E402


def _make_coordinator(entry_data: dict, stored: dict):
    hass = MagicMock()
    store = MagicMock()
    store.async_load = MagicMock(
        side_effect=lambda: asyncio.ensure_future(_ret(stored))
    )
    entry = SimpleNamespace(data=entry_data, options={}, entry_id="test-entry")
    coord = DwellantCoordinator.__new__(DwellantCoordinator)
    coord.hass = hass
    coord._store = store
    coord._entry = entry
    coord._clients = {}
    coord.data = {}
    coord._digest_hashes = {}
    coord._gmail_cache = {}
    coord._gmail_searched = set()
    return coord


async def _ret(value):
    return value


def _imap_result(mapping: dict):
    async def fake_fetch(self, codes):
        return {
            c: mapping.get(c, {"email_found": False, "description": None})
            for c in codes
        }

    return fake_fetch


def test_restart_researches_all_codes_including_collected():
    entry_data = {
        "users": [
            {"email": "user@example.com", "imap_password": "app-pass"},
        ]
    }
    stored = {
        "user@example.com": {
            "available": {"AAA": {"code": "AAA", "email_found": False}},
            "history": {
                "OLD": {"code": "OLD", "status": "collected"},
            },
            "last_updated": "2026-11-13T00:00:00+00:00",
        }
    }
    coord = _make_coordinator(entry_data, stored)
    # _async_enrich_descriptions reads previous state from self.data,
    # which _async_update_data populates from the persisted store.
    coord.data = stored

    seen = {}

    async def fake_fetch(self, codes):
        seen["codes"] = sorted(codes)
        return {c: {"email_found": True, "description": "note"} for c in codes}

    with patch(
        "custom_components.dwellant_packages.gmail.ImapEnrichmentClient"
        ".async_fetch_descriptions",
        fake_fetch,
    ):
        enriched = asyncio.run(
            coord._async_enrich_descriptions(
                "user@example.com", {"AAA": {"code": "AAA"}}
            )
        )

    # Both the available code and the collected-history code re-searched.
    assert seen["codes"] == ["AAA", "OLD"]
    assert enriched["AAA"]["description"] == "note"
    assert enriched["AAA"]["email_found"] is True


def test_email_found_codes_skipped_within_run():
    entry_data = {
        "users": [{"email": "user@example.com", "imap_password": "app-pass"}]
    }
    coord = _make_coordinator(entry_data, {})
    coord.data = {
        "user@example.com": {
            "available": {
                "AAA": {"code": "AAA", "email_found": True, "description": "kept"}
            },
            "history": {},
        }
    }

    calls = []

    async def fake_fetch(self, codes):
        calls.append(list(codes))
        return {c: {"email_found": True, "description": "new"} for c in codes}

    with patch(
        "custom_components.dwellant_packages.gmail.ImapEnrichmentClient"
        ".async_fetch_descriptions",
        fake_fetch,
    ):
        enriched = asyncio.run(
            coord._async_enrich_descriptions(
                "user@example.com", {"AAA": {"code": "AAA"}}
            )
        )

    assert calls == []  # already flagged -> no inbox query
    # Skipped codes pass through untouched; the flag + description are
    # re-attached downstream by merge_user_state from persisted state.
    assert enriched == {"AAA": {"code": "AAA"}}
