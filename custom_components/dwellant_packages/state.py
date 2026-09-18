"""Pure per-user package state helpers (no Home Assistant imports).

Tested without HA; used by the coordinator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_utc() -> datetime:
    """Current UTC time (patchable in tests)."""
    return datetime.now(timezone.utc)


def diff_available(
    old: dict[str, dict], new: dict[str, dict]
) -> tuple[list[dict], list[str]]:
    """Diff available packages by collection code.

    Returns (arrived_packages, collected_codes). Both lists sorted by code
    for stable event/notification ordering.
    """
    arrived = [new[code] for code in sorted(set(new) - set(old))]
    collected = sorted(set(old) - set(new))
    return arrived, collected


def prune_history(
    history: dict[str, dict],
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Drop collected entries older than retention. 0 = keep forever."""
    if retention_days <= 0:
        return history
    ref = now or now_utc()
    cutoff = ref - timedelta(days=retention_days)
    kept: dict[str, dict] = {}
    for code, item in history.items():
        if item.get("status") != "collected":
            kept[code] = item
            continue
        collected_at = item.get("collected_at")
        try:
            when = (
                datetime.fromisoformat(collected_at)
                if collected_at
                else None
            )
        except (ValueError, TypeError):
            when = None
        if when is None or when >= cutoff:
            kept[code] = item
    return kept


def merge_user_state(
    previous: dict | None,
    fetched: dict[str, dict],
    retention_days: int,
    timestamp: str,
) -> tuple[dict, list[dict], list[dict]]:
    """Merge a successful fetch into stored per-user state.

    Returns (new_state, arrived, collected) where collected items carry
    collected_at=timestamp. On first sight (no previous state) nothing is
    reported as arrived/collected (suppresses boot spam).
    """
    prev_available = (previous or {}).get("available", {}) or {}
    prev_history = dict((previous or {}).get("history", {}) or {})
    first_seen = previous is None

    if first_seen:
        arrived: list[dict] = []
        collected: list[dict] = []
    else:
        arrived_pkgs, collected_codes = diff_available(prev_available, fetched)
        arrived = arrived_pkgs
        collected = []
        for code in collected_codes:
            item = dict(prev_history.get(code, prev_available.get(code, {})))
            item.update(
                {"code": code, "status": "collected", "collected_at": timestamp}
            )
            collected.append(item)

    history = dict(prev_history)
    for code, pkg in fetched.items():
        item = dict(history.get(code, {}))
        item.update(pkg)
        item.update(
            {
                "code": code,
                "status": "available",
                "first_seen": item.get("first_seen", timestamp),
                "last_seen": timestamp,
            }
        )
        history[code] = item
    for item in collected:
        history[item["code"]] = item

    history = prune_history(history, retention_days)

    state = {
        "available": fetched,
        "history": history,
        "last_updated": timestamp,
    }
    return state, arrived, collected
