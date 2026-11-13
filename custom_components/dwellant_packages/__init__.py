"""The Dwellant Packages integration (single entry, multi-user)."""

from __future__ import annotations

from pathlib import Path

from ha_deploy_kit.ha import (
    async_clear_restart_issue,
    async_register_restart_service,
    register_card_serving,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

from .const import DOMAIN, PLATFORMS, STORAGE_VERSION
from .coordinator import DwellantCoordinator

CARD_URL = "/dwellant_packages/dwellant-packages-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the restart service and serve the Lovelace card (global setup).

    The service is registered idempotently with an empty schema and raises a
    one-click (fixable) "Restart required" Repairs issue — the deploy script
    calls it over the HA API after a backend change. The card is served from
    the component's ``lovelace`` dir at a stable URL.
    """
    async_register_restart_service(hass, DOMAIN, fixable=True)
    lovelace_dir = str(Path(__file__).parent / "lovelace")
    register_card_serving(hass, DOMAIN, lovelace_dir, CARD_URL)
    return True


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
    # The new code is now running, so clear any pending restart notice.
    async_clear_restart_issue(hass, DOMAIN)
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
