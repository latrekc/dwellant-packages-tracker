"""Local digest-aggregation test (stdlib only, no HA / network / pip needed).

Stubs `aiohttp`, `bs4` and the `homeassistant.*` modules, then drives the
REAL state.py + coordinator digest helpers through a scripted scenario:

  poll 1: first sight, 2 packages            -> NO notification (silent)
  poll 2: +3 arrive at once (batch delivery) -> ONE digest, 5 waiting
  poll 3: 4 collected at once (bulk pickup)  -> ONE digest, 1 waiting + 4 recent
  poll 4: nothing waiting/recent              -> digest DISMISSED
  toggles: notify_collection off             -> collected half hidden

Usage: python3 tests/local_digest_check.py
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- stubs
aiohttp = types.ModuleType("aiohttp")


class _ClientError(Exception):
    pass


aiohttp.ClientError = _ClientError
aiohttp.ClientSession = object
aiohttp.CookieJar = object
sys.modules["aiohttp"] = aiohttp

bs4 = types.ModuleType("bs4")


class _BeautifulSoup:
    def __init__(self, *a, **k):
        pass

    def find_all(self, *a, **k):
        return []

    def find(self, *a, **k):
        return None


bs4.BeautifulSoup = _BeautifulSoup
sys.modules["bs4"] = bs4

integration_pkg = "custom_components.dwellant_packages"
pkg = types.ModuleType("custom_components.dwellant_packages")
pkg.__path__ = [str(ROOT / "custom_components" / "dwellant_packages")]
sys.modules["custom_components.dwellant_packages"] = pkg


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(m, key, value)
    sys.modules[name] = m
    return m


class _FakeLogger:
    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


logging_stub = _mod("logging", getLogger=lambda *a, **k: _FakeLogger())
sys.modules["logging"] = logging_stub

ha = _mod("homeassistant")
_mod("homeassistant.config_entries", ConfigEntry=object)
_mod("homeassistant.core", HomeAssistant=object)
_mod(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
)
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.storage", Store=object)


class _DataUpdateCoordinator:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, hass, logger=None, *, name=None, update_interval=None):
        self.hass = hass
        self.update_interval = update_interval
        self.data = {}

    def __class_getitem__(cls, item):
        return cls


_mod(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinator,
    UpdateFailed=type("UpdateFailed", (Exception,), {}),
)

from custom_components.dwellant_packages import (  # noqa: E402  # noqa: E402
    coordinator as coord_mod,
    state as state_mod,
)
from custom_components.dwellant_packages.const import (  # noqa: E402
    CONF_NOTIFY_ARRIVAL,
    CONF_NOTIFY_COLLECTION,
)

# ---------------------------------------------------------------- fakes
NOW = datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc)


def pkg(code, type_label="Package (Medium)", day=18, hour=15, minute=24):
    iso = f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00"
    return {
        "code": code,
        "unit": "12 Example House",
        "type": type_label,
        "type_slug": "package (medium)",
        "icon": "mdi:package",
        "delivery_time": iso,
        "delivery_time_raw": f"today, {day:02d}/09/2026 at {hour:02d}:{minute:02d}",
        "concierge": "Concierge",
    }


class FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, data))


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data):
        self.calls.append((domain, service, data))


class FakeHass:
    def __init__(self):
        self.bus = FakeBus()
        self.services = FakeServices()
        self.tasks = []

    def async_create_task(self, coro):
        # HA would schedule this; the harness awaits it in run_poll.
        self.tasks.append(coro)


class FakeEntry:
    entry_id = "test-entry"
    data = {"users": []}
    options = {}


async def run_poll(hass, entry, previous, fetched, opts):
    """Drive one coordinator poll worth of merge + digest, return new state."""
    timestamp = NOW.isoformat()
    state, arrived, collected = state_mod.merge_user_state(
        previous, fetched, 30, timestamp
    )
    coordinator = coord_mod.DwellantCoordinator.__new__(coord_mod.DwellantCoordinator)
    coordinator.hass = hass
    coordinator._entry = entry
    coordinator._clients = {}
    if previous is not None:
        coordinator._fire_events("user@example.com", state, arrived, collected, opts)
    # Drain tasks HA would have scheduled (service calls are coroutines here).
    for task in hass.tasks:
        if asyncio.iscoroutine(task):
            await task
    hass.tasks.clear()
    return state


def notifications(hass):
    return [call for call in hass.services.calls if call[1] in ("create", "dismiss")]


def show(title, hass):
    print(f"--- {title}")
    notes = notifications(hass)
    print(f"  service calls: {len(notes)}")
    for domain, service, data in notes:
        print(f"  [{domain}.{service}] id={data.get('notification_id')}")
        if "title" in data:
            print(f"    title: {data['title']}")
        if "message" in data:
            for line in data["message"].splitlines():
                print(f"    | {line}")
    print()


def main():
    entry = FakeEntry()
    opts = {CONF_NOTIFY_ARRIVAL: True, CONF_NOTIFY_COLLECTION: True}
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {name} {detail}")
        if not condition:
            failures.append(name)

    # Poll 1: first sight -> silent.
    hass = FakeHass()
    s1 = asyncio.run(
        run_poll(hass, entry, None, {"A1": pkg("A1"), "B2": pkg("B2")}, opts)
    )
    check("poll1 silent on first sight", notifications(hass) == [])
    check(
        "poll1 no per-package events on first sight",
        hass.bus.events == [],
        f"(got {len(hass.bus.events)})",
    )

    # Poll 2: batch delivery of 3 at once -> ONE digest.
    hass = FakeHass()
    fetched2 = {
        "A1": pkg("A1"),
        "B2": pkg("B2"),
        "C3": pkg("C3", "A letter", hour=16),
        "D4": pkg("D4", "Package (Small)", hour=16, minute=5),
        "E5": pkg("E5", "Oversized/Heavy", hour=16, minute=5),
    }
    s2 = asyncio.run(run_poll(hass, entry, s1, fetched2, opts))
    notes = notifications(hass)
    check("poll2 exactly ONE service call", len(notes) == 1, f"(got {len(notes)})")
    if notes:
        _, service, data = notes[0]
        check("poll2 call is create", service == "create")
        check(
            "poll2 title counts 5 waiting", "5 waiting" in data["title"], data["title"]
        )
        check(
            "poll2 message lists all 5",
            data["message"].count("•") == 5,
            f"(bullets={data['message'].count('•')})",
        )
        check(
            "poll2 no codes in message",
            "A1" not in data["message"] and "C3" not in data["message"],
        )
    new_pkg_events = [e for e in hass.bus.events if e[0] == "dwellant_new_package"]
    check("poll2 3 per-package arrival events", len(new_pkg_events) == 3)
    update_events = [e for e in hass.bus.events if e[0] == "dwellant_packages_update"]
    check("poll2 one update event", len(update_events) == 1)
    if update_events:
        check(
            "poll2 update counts",
            update_events[0][1]["arrived_count"] == 3
            and update_events[0][1]["available_count"] == 5,
            str(update_events[0][1]),
        )
    show("poll2 digest (batch delivery)", hass)

    # Poll 3: bulk pickup of 4 -> ONE digest, 1 waiting + 4 recent.
    hass = FakeHass()
    s2_hist = dict(s2["history"])
    for code in ("A1", "B2", "C3", "D4"):
        s2_hist[code] = {
            **s2_hist[code],
            "status": "collected",
            "collected_at": (NOW - timedelta(hours=1)).isoformat(),
        }
    s2_collected = {**s2, "history": s2_hist}
    fetched3 = {"E5": pkg("E5", "Oversized/Heavy", hour=16, minute=5)}
    s3 = asyncio.run(run_poll(hass, entry, s2_collected, fetched3, opts))
    notes = notifications(hass)
    check("poll3 exactly ONE service call", len(notes) == 1, f"(got {len(notes)})")
    if notes:
        _, service, data = notes[0]
        check(
            "poll3 title 1 waiting + 4 collected",
            "1 waiting" in data["title"] and "4 collected" in data["title"],
            data["title"],
        )
        check(
            "poll3 message has both sections",
            "Waiting for pickup (1)" in data["message"]
            and "Recently collected (4)" in data["message"],
        )
    collected_events = [
        e for e in hass.bus.events if e[0] == "dwellant_package_collected"
    ]
    check("poll3 4 per-package collected events", len(collected_events) == 4)
    show("poll3 digest (bulk pickup)", hass)

    # Poll 4: nothing waiting, nothing recent -> dismiss.
    hass = FakeHass()
    old_hist = {}
    for code, item in s3["history"].items():
        item = dict(item)
        if item.get("status") == "collected":
            item["collected_at"] = (NOW - timedelta(hours=30)).isoformat()
        old_hist[code] = item
    s3_old = {"available": {}, "history": old_hist, "last_updated": NOW.isoformat()}
    asyncio.run(run_poll(hass, entry, s3_old, {}, opts))
    notes = notifications(hass)
    check("poll4 exactly ONE service call", len(notes) == 1)
    if notes:
        check("poll4 call is dismiss", notes[0][1] == "dismiss", notes[0][1])
    show("poll4 dismiss (nothing to show)", hass)

    # Poll 5: collection notifications off -> waiting only.
    hass = FakeHass()
    opts_off = {CONF_NOTIFY_ARRIVAL: True, CONF_NOTIFY_COLLECTION: False}
    asyncio.run(run_poll(hass, entry, s2_collected, fetched3, opts_off))
    notes = notifications(hass)
    check("poll5 ONE service call", len(notes) == 1)
    if notes:
        _, _, data = notes[0]
        check(
            "poll5 collected half hidden",
            "Recently collected" not in data.get("message", "")
            and "collected" not in data.get("title", ""),
            data.get("title", ""),
        )
    show("poll5 digest (collection toggle off)", hass)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
