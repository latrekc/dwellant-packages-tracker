"""Tests for state.py diff/merge/prune logic (no HA needed)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.dwellant_packages.state import (  # noqa: E402
    diff_available,
    merge_user_state,
    now_utc,
    prune_history,
)

PKG_A = {"code": "AAA", "unit": "U1", "type": "Package (Small)"}
PKG_B = {"code": "BBB", "unit": "U1", "type": "A letter"}


def test_diff_arrived_collected():
    arrived, collected = diff_available({"AAA": PKG_A}, {"AAA": PKG_A, "BBB": PKG_B})
    assert [p["code"] for p in arrived] == ["BBB"]
    assert collected == []
    arrived, collected = diff_available(
        {"AAA": PKG_A, "BBB": PKG_B}, {"AAA": PKG_A}
    )
    assert arrived == [] and collected == ["BBB"]


def test_first_sight_suppresses_events():
    state, arrived, collected = merge_user_state(None, {"AAA": PKG_A}, 30, "2026-09-18T10:00:00+00:00")
    assert arrived == [] and collected == []
    assert state["available"] == {"AAA": PKG_A}
    assert state["history"]["AAA"]["status"] == "available"
    assert state["history"]["AAA"]["first_seen"] == "2026-09-18T10:00:00+00:00"


def _ts(days_ago: int = 0) -> str:
    """ISO timestamp N days before now (keeps retention windows valid)."""
    from datetime import timedelta

    return (now_utc() - timedelta(days=days_ago)).isoformat()


def test_arrival_and_collection():
    prev = {
        "available": {"AAA": PKG_A},
        "history": {"AAA": {**PKG_A, "status": "available"}},
    }
    state, arrived, collected = merge_user_state(
        prev, {"AAA": PKG_A, "BBB": PKG_B}, 30, _ts(2)
    )
    assert [p["code"] for p in arrived] == ["BBB"]
    assert collected == []
    state2, arrived2, collected2 = merge_user_state(
        state, {"BBB": PKG_B}, 30, _ts(1)
    )
    assert arrived2 == []
    assert [p["code"] for p in collected2] == ["AAA"]
    assert state2["history"]["AAA"]["status"] == "collected"
    assert state2["history"]["AAA"]["collected_at"] == _ts(1) or state2["history"]["AAA"]["collected_at"].startswith(_ts(1)[:16])
    # Collected history retained, not deleted.
    assert "AAA" in state2["history"]


def test_prune_retention():
    history = {
        "OLD": {"code": "OLD", "status": "collected", "collected_at": _ts(60)},
        "NEW": {"code": "NEW", "status": "collected", "collected_at": _ts(1)},
        "AVL": {"code": "AVL", "status": "available"},
    }
    now = now_utc()
    pruned = prune_history(history, 30, now)
    assert set(pruned) == {"NEW", "AVL"}
    assert prune_history(history, 0, now) == history


def test_description_preserved_when_fetch_lacks_it():
    prev = {
        "available": {"AAA": {**PKG_A, "description": "A Miele package - DPD"}},
        "history": {"AAA": {**PKG_A, "status": "available", "description": "A Miele package - DPD"}},
    }
    fetched = {"AAA": dict(PKG_A)}  # fresh portal fetch, no description
    state, _, _ = merge_user_state(prev, fetched, 30, _ts())
    assert state["available"]["AAA"]["description"] == "A Miele package - DPD"
    assert state["history"]["AAA"]["description"] == "A Miele package - DPD"


def test_description_overwrite_and_collected_retention():
    prev = {
        "available": {"AAA": {**PKG_A, "description": "old note"}},
        "history": {"AAA": {**PKG_A, "status": "available", "description": "old note"}},
    }
    fetched = {"AAA": {**PKG_A, "description": "new note"}}
    state, _, _ = merge_user_state(prev, fetched, 30, _ts(1))
    assert state["available"]["AAA"]["description"] == "new note"
    state2, _, collected = merge_user_state(state, {}, 30, _ts())
    assert collected[0]["description"] == "new note"
    assert state2["history"]["AAA"]["description"] == "new note"


def test_email_found_flag_sticky():
    prev = {
        "available": {"AAA": {**PKG_A, "email_found": True, "description": None}},
        "history": {"AAA": {**PKG_A, "status": "available", "email_found": True}},
    }
    fetched = {"AAA": dict(PKG_A)}  # portal fetch carries no flag
    state, _, _ = merge_user_state(prev, fetched, 30, _ts())
    assert state["available"]["AAA"]["email_found"] is True
    assert state["history"]["AAA"]["email_found"] is True


def test_email_found_defaults_absent():
    state, _, _ = merge_user_state(None, {"AAA": PKG_A}, 30, _ts())
    assert "email_found" not in state["available"]["AAA"]
