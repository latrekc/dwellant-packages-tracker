"""Polling coordinator: one entry, one client per user, per-user state."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import storage
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, DwellantClient, InvalidAuth
from .const import (
    COLLECTED_RECENT_HOURS,
    CONF_BASE_URL,
    CONF_NOTIFY_ARRIVAL,
    CONF_NOTIFY_COLLECTION,
    CONF_ORG_ID,
    CONF_PASSWORD,
    CONF_RETENTION_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_USERS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    EVENT_NEW_PACKAGE,
    EVENT_PACKAGE_COLLECTED,
    EVENT_PACKAGES_UPDATE,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
    normalize_base_url,
)
from .state import merge_user_state, now_utc

_LOGGER = logging.getLogger(__name__)


def _describe_package(pkg: dict, when: str) -> str:
    """Human summary without collection codes: 'Package (Medium), 18/09/2026 at 15:24'."""
    type_label = pkg.get("type") or "Package"
    when = (when or "").strip()
    return f"{type_label}, {when}" if when else str(type_label)


def _format_collected_at(iso_value: str | None) -> str:
    """Render collected_at ISO to the portal's 'DD/MM/YYYY at HH:MM' style."""
    if not iso_value:
        return ""
    text = str(iso_value).strip()
    try:
        when = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return text
    return when.strftime("%d/%m/%Y at %H:%M")


def _recently_collected(
    history: dict, hours: int = COLLECTED_RECENT_HOURS
) -> list[dict]:
    """Collected items from the last `hours`, newest first (for the digest)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for item in history.values():
        if item.get("status") != "collected":
            continue
        try:
            when = datetime.fromisoformat(item.get("collected_at") or "")
        except (ValueError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            recent.append(item)
    recent.sort(key=lambda i: i.get("collected_at") or "", reverse=True)
    return recent


def _options(entry: ConfigEntry) -> dict:
    data = entry.data or {}
    opts = entry.options or {}
    scan = opts.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, 5))
    try:
        scan = int(scan)
    except (TypeError, ValueError):
        scan = 5
    scan = max(MIN_SCAN_MINUTES, min(MAX_SCAN_MINUTES, scan))
    retention = opts.get(
        CONF_RETENTION_DAYS, data.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
    )
    try:
        retention = int(retention)
    except (TypeError, ValueError):
        retention = DEFAULT_RETENTION_DAYS
    return {
        CONF_SCAN_INTERVAL: scan,
        CONF_RETENTION_DAYS: max(0, retention),
        CONF_NOTIFY_ARRIVAL: bool(
            opts.get(CONF_NOTIFY_ARRIVAL, data.get(CONF_NOTIFY_ARRIVAL, True))
        ),
        CONF_NOTIFY_COLLECTION: bool(
            opts.get(CONF_NOTIFY_COLLECTION, data.get(CONF_NOTIFY_COLLECTION, True))
        ),
    }


class DwellantCoordinator(DataUpdateCoordinator[dict]):
    """Coordinator holding {email: {available, history, last_updated}}."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: storage.Store,
        entry: ConfigEntry,
    ) -> None:
        self._store = store
        self._entry = entry
        self._clients: dict[str, DwellantClient] = {}
        opts = _options(entry)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=opts[CONF_SCAN_INTERVAL]),
        )
        self._sync_clients()
        self.data: dict = {}

    def _sync_clients(self) -> None:
        """Create/drop per-user clients to match entry users (no YAML)."""
        users = self._entry.data.get(CONF_USERS, [])
        wanted = {
            str(u.get("email", "")).strip().lower(): u for u in users if u.get("email")
        }
        for email in list(self._clients):
            if email not in wanted:
                old_client = self._clients.pop(email)
                self.hass.async_create_task(old_client.async_close())
        for email, user in wanted.items():
            org_id = user.get(CONF_ORG_ID)
            base_url = normalize_base_url(str(user.get(CONF_BASE_URL, "")))
            if org_id in (None, "") or not base_url:
                _LOGGER.warning(
                    "Skipping Dwellant user %s: portal address or "
                    "organisation ID missing",
                    email,
                )
                continue
            client = self._clients.get(email)
            if client is None:
                # No shared session: each client owns an isolated cookie jar.
                self._clients[email] = DwellantClient(
                    email=user.get("email", ""),
                    password=user.get(CONF_PASSWORD, ""),
                    org_id=org_id,
                    base_url=base_url,
                )
            else:
                if user.get(CONF_PASSWORD, "") != getattr(client, "_password", None):
                    client.update_password(user.get(CONF_PASSWORD, ""))
                client.update_org_id(org_id)
                client.update_base_url(base_url)

    async def async_initialize(self) -> None:
        """Load persisted state, refresh interval, do first (silent) refresh."""
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self.data = stored
        opts = _options(self._entry)
        self.update_interval = timedelta(minutes=opts[CONF_SCAN_INTERVAL])
        await self.async_config_entry_first_refresh()

    async def _async_update_data(self) -> dict:
        self._sync_clients()
        opts = _options(self._entry)
        self.update_interval = timedelta(minutes=opts[CONF_SCAN_INTERVAL])
        timestamp = now_utc().isoformat()
        new_data: dict = {}
        for email, client in self._clients.items():
            try:
                fetched = await client.async_fetch_available()
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(
                    f"Dwellant auth failed for {email}"
                ) from err
            except CannotConnect as err:
                raise UpdateFailed(f"Dwellant fetch failed for {email}: {err}") from err
            previous = self.data.get(email)
            state, arrived, collected = merge_user_state(
                previous, fetched, opts[CONF_RETENTION_DAYS], timestamp
            )
            new_data[email] = state
            if previous is not None:  # never notify on first sight
                self._fire_events(email, state, arrived, collected, opts)
        self.data = new_data
        # Persist without blocking the refresh on storage errors.
        try:
            await self._store.async_save(self.data)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not persist Dwellant package state", exc_info=True)
        return self.data

    def _fire_events(
        self,
        email: str,
        state: dict,
        arrived: list[dict],
        collected: list[dict],
        opts: dict,
    ) -> None:
        # NOTE: collection codes stay in event payloads as stable tracking
        # keys, but are never shown in notification titles/messages.
        # Notifications are AGGREGATED: one persistent notification per user
        # (same notification_id, overwritten each poll) summarizing everything
        # currently waiting + recently collected — no per-package spam when a
        # batch is delivered or picked up at once.
        hass = self.hass
        entry_id = self._entry.entry_id
        for pkg in arrived:
            hass.bus.async_fire(
                EVENT_NEW_PACKAGE,
                {
                    "entry_id": entry_id,
                    "email": email,
                    "code": pkg.get("code"),
                    "unit": pkg.get("unit"),
                    "type": pkg.get("type"),
                    "delivery_time": pkg.get("delivery_time"),
                    "delivery_time_raw": pkg.get("delivery_time_raw"),
                    "concierge": pkg.get("concierge"),
                },
            )
        for pkg in collected:
            hass.bus.async_fire(
                EVENT_PACKAGE_COLLECTED,
                {
                    "entry_id": entry_id,
                    "email": email,
                    "code": pkg.get("code"),
                    "unit": pkg.get("unit"),
                    "type": pkg.get("type"),
                    "delivery_time": pkg.get("delivery_time"),
                    "delivery_time_raw": pkg.get("delivery_time_raw"),
                    "collected_at": pkg.get("collected_at"),
                },
            )
        hass.bus.async_fire(
            EVENT_PACKAGES_UPDATE,
            {
                "entry_id": entry_id,
                "email": email,
                "arrived_count": len(arrived),
                "collected_count": len(collected),
                "available_count": len(state.get("available", {})),
            },
        )
        self._async_update_digest(email, state, opts)

    def _async_update_digest(self, email: str, state: dict, opts: dict) -> None:
        """Rebuild the single per-user digest notification (overwrite, not stack)."""
        hass = self.hass
        entry_id = self._entry.entry_id
        available = state.get("available", {})
        waiting = sorted(
            available.values(),
            key=lambda p: (p.get("delivery_time") or "", p.get("code") or ""),
        )
        recent_collected = _recently_collected(state.get("history", {}))

        show_waiting = bool(waiting) and opts[CONF_NOTIFY_ARRIVAL]
        show_collected = bool(recent_collected) and opts[CONF_NOTIFY_COLLECTION]
        notification_id = f"dwellant_{entry_id}_{email}_digest"

        if not show_waiting and not show_collected:
            # Nothing to show: dismiss a stale digest instead of stacking.
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": notification_id},
                )
            )
            return

        lines = []
        if show_waiting:
            lines.append(
                f"Waiting for pickup ({len(waiting)}):\n"
                + "\n".join(
                    f"• {_describe_package(p, p.get('delivery_time_raw') or '')}"
                    for p in waiting
                )
            )
        if show_collected:
            lines.append(
                f"Recently collected ({len(recent_collected)}):\n"
                + "\n".join(
                    f"• {_describe_package(p, _format_collected_at(p.get('collected_at')))}"
                    for p in recent_collected
                )
            )
        title_bits = []
        if show_waiting:
            title_bits.append(f"{len(waiting)} waiting")
        if show_collected:
            title_bits.append(f"{len(recent_collected)} collected")
        hass.async_create_task(
            hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": f"Dwellant ({email}): {', '.join(title_bits)}",
                    "message": "\n\n".join(lines),
                    "notification_id": notification_id,
                },
            )
        )

    async def async_shutdown(self) -> None:
        """Close all owned per-user sessions (on entry unload)."""
        for client in self._clients.values():
            await client.async_close()

    @property
    def options(self) -> dict:
        """Current effective options."""
        return _options(self._entry)

    @property
    def emails(self) -> list[str]:
        """Configured user emails (display order)."""
        return list(self._clients.keys())

    def user_email(self, email_key: str) -> str:
        """Original-case email for a client key."""
        client = self._clients.get(email_key)
        return client.email if client else email_key

    def user_org_id(self, email_key: str) -> int | None:
        """Configured org ID for a user key (None when not set)."""
        client = self._clients.get(email_key)
        if client is not None:
            try:
                return int(client.org_id)
            except (TypeError, ValueError):
                return None
        for user in self._entry.data.get(CONF_USERS, []):
            if str(user.get("email", "")).strip().lower() == email_key:
                try:
                    return int(user[CONF_ORG_ID])
                except (KeyError, TypeError, ValueError):
                    return None
        return None

    def user_base_url(self, email_key: str) -> str:
        """Configured portal address for a user key."""
        client = self._clients.get(email_key)
        if client is not None:
            return str(getattr(client, "base_url", ""))
        for user in self._entry.data.get(CONF_USERS, []):
            if str(user.get("email", "")).strip().lower() == email_key:
                return normalize_base_url(str(user.get(CONF_BASE_URL, "")))
        return ""

    @property
    def retention_days(self) -> int:
        """Effective retention days."""
        return _options(self._entry)[CONF_RETENTION_DAYS]
