"""The Dwellant Packages integration (single entry, multi-user)."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

from .const import DOMAIN, PLATFORMS, STORAGE_VERSION
from .coordinator import DwellantCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dwellant Packages from a config entry.

    One entry holds all users (entry.data[CONF_USERS]); each user gets its
    own device + sensor entities. No YAML, no hardcoded credentials.
    """
    hass.data.setdefault(DOMAIN, {})

    # Note: each user client owns an isolated aiohttp session (per-user jars).
    store = storage.Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    coordinator = DwellantCoordinator(hass, store, entry)
    await coordinator.async_initialize()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_card(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when data/options change (users, interval, toggles)."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card as an extra frontend module (best effort)."""
    try:
        from homeassistant.components import frontend

        lovelace_dir = str(Path(__file__).parent / "lovelace")
        card_url = "/dwellant_packages/dwellant-packages-card.js"
        try:
            hass.http.register_static_path(
                "/dwellant_packages", lovelace_dir, cache_headers=False
            )
        except (RuntimeError, ValueError):
            pass
        try:
            frontend.add_extra_module_url(hass, card_url)
        except (RuntimeError, ValueError):
            pass
    except Exception:  # noqa: BLE001 - frontend is optional in tests
        _LOGGER.debug("Frontend card auto-registration skipped")
