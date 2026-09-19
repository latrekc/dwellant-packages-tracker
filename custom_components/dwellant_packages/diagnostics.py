"""Diagnostics for the Dwellant Packages integration (redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_USERS, DOMAIN
from .coordinator import DwellantCoordinator

TO_REDACT = {"password", "cookie", "cookies", "token", "authorization", "set-cookie"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics (counts + codes only)."""
    coordinator: DwellantCoordinator = hass.data[DOMAIN][entry.entry_id]
    users = []
    for user in entry.data.get(CONF_USERS, []):
        email = str(user.get("email", ""))
        key = email.strip().lower()
        users.append(
            {
                "email": f"{email[:2]}***" if email else "***",
                "org_id": coordinator.user_org_id(key),
                "portal": coordinator.user_base_url(key),
            }
        )
    packages: dict[str, Any] = {}
    for email_key, state in (coordinator.data or {}).items():
        available = state.get("available", {})
        history = state.get("history", {})
        collected = sum(1 for i in history.values() if i.get("status") == "collected")
        packages[email_key] = {
            "available_count": len(available),
            "available_codes": sorted(available.keys()),
            "collected_count": collected,
            "last_updated": state.get("last_updated"),
        }
    return {
        "users": users,
        "options": dict(entry.options),
        "packages": packages,
    }
