"""Tests for state.py diff/merge/prune logic (no HA needed)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.dwellant_packages.state import (  # noqa: E402
    diff_available,
    merge_user_state,
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


def test_arrival_and_collection():
    prev = {
        "available": {"AAA": PKG_A},
        "history": {"AAA": {**PKG_A, "status": "available"}},
    }
    state, arrived, collected = merge_user_state(
        prev, {"AAA": PKG_A, "BBB": PKG_B}, 30, "2026-09-18T11:00:00+00:00"
    )
    assert [p["code"] for p in arrived] == ["BBB"]
    assert collected == []
    state2, arrived2, collected2 = merge_user_state(
        state, {"BBB": PKG_B}, 30, "2026-09-18T12:00:00+00:00"
    )
    assert arrived2 == []
    assert [p["code"] for p in collected2] == ["AAA"]
    assert state2["history"]["AAA"]["status"] == "collected"
    assert state2["history"]["AAA"]["collected_at"] == "2026-09-18T12:00:00+00:00"
    # Collected history retained, not deleted.
    assert "AAA" in state2["history"]


def test_prune_retention():
    history = {
        "OLD": {"code": "OLD", "status": "collected", "collected_at": "2026-01-01T00:00:00+00:00"},
        "NEW": {"code": "NEW", "status": "collected", "collected_at": "2026-09-17T00:00:00+00:00"},
        "AVL": {"code": "AVL", "status": "available"},
    }
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    pruned = prune_history(history, 30, now)
    assert set(pruned) == {"NEW", "AVL"}
    assert prune_history(history, 0, now) == history
