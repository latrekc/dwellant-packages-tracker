"""Sensor platform: one count sensor per Dwellant user (device per user)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BASE_URL,
    ATTR_COLLECTED,
    ATTR_EMAIL,
    ATTR_LAST_UPDATED,
    ATTR_ORG_ID,
    ATTR_PACKAGES,
    ATTR_RETENTION_DAYS,
    ATTR_UNIT,
    CONF_RETENTION_DAYS,
    DOMAIN,
    STATUS_AVAILABLE,
    STATUS_COLLECTED,
)
from .coordinator import DwellantCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one sensor per user in the entry."""
    coordinator: DwellantCoordinator = hass.data[DOMAIN][entry.entry_id]
    _async_cleanup_removed_users(hass, entry, coordinator)
    async_add_entities(
        DwellantPackagesSensor(coordinator, entry, email_key)
        for email_key in coordinator.emails
    )


def _async_cleanup_removed_users(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: DwellantCoordinator
) -> None:
    """Remove entities/devices of users deleted via Options."""
    wanted = {f"{entry.entry_id}_{email}_available" for email in coordinator.emails}
    ent_reg = er.async_get(hass)
    for entity_id, reg_entry in list(ent_reg.entities.items()):
        if (
            reg_entry.config_entry_id == entry.entry_id
            and reg_entry.platform == DOMAIN
            and reg_entry.unique_id not in wanted
        ):
            ent_reg.async_remove(entity_id)
    dev_reg = dr.async_get(hass)
    wanted_devices = {
        f"{entry.entry_id}_{email}" for email in coordinator.emails
    }
    for device in list(dev_reg.devices.values()):
        identifiers = {i for i in device.identifiers if i[0] == DOMAIN}
        if not identifiers:
            continue
        if not any(i[1].startswith(entry.entry_id) for i in identifiers):
            continue
        if not any(i[1] in wanted_devices for i in identifiers):
            dev_reg.async_remove_device(device.id)


def _sorted_packages(available: dict) -> list[dict]:
    pkgs = list(available.values())
    pkgs.sort(
        key=lambda p: (
            p.get("delivery_time") or "",
            p.get("delivery_time_raw") or "",
            p.get("code") or "",
        )
    )
    return [
        {
            "code": p.get("code"),
            "unit": p.get("unit"),
            "type": p.get("type"),
            "delivery_time": p.get("delivery_time"),
            "delivery_time_raw": p.get("delivery_time_raw"),
            "concierge": p.get("concierge"),
            "icon": p.get("icon"),
        }
        for p in pkgs
    ]


class DwellantPackagesSensor(CoordinatorEntity[DwellantCoordinator], SensorEntity):
    """Available-package count for one Dwellant user."""

    _attr_icon = "mdi:package-variant-closed"
    _attr_translation_key = "available_packages"

    def __init__(
        self, coordinator: DwellantCoordinator, entry: ConfigEntry, email_key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._email_key = email_key
        email = coordinator.user_email(email_key)
        self._attr_unique_id = f"{entry.entry_id}_{email_key}_available"
        # Device keeps "Dwellant <email>" so renames at device level
        # propagate; entity itself is just "Packages" (HA shows
        # "<device> <entity>" = "Dwellant <email> Packages").
        self._attr_name = "Packages"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{email_key}")},
            name=f"Dwellant {email}",
            manufacturer="Dwellant",
            model="Resident portal",
        )

    @property
    def native_value(self) -> int:
        """Number of available packages."""
        state = (self.coordinator.data or {}).get(self._email_key, {})
        return len(state.get("available", {}))

    @property
    def extra_state_attributes(self) -> dict:
        """Full package lists; the Lovelace card reads these."""
        state = (self.coordinator.data or {}).get(self._email_key, {})
        available = state.get("available", {})
        history = state.get("history", {})
        collected = sorted(
            (i for i in history.values() if i.get("status") == STATUS_COLLECTED),
            key=lambda i: i.get("collected_at") or "",
            reverse=True,
        )
        units = sorted({p.get("unit") for p in available.values() if p.get("unit")})
        return {
            ATTR_PACKAGES: _sorted_packages(available),
            ATTR_COLLECTED: [
                {
                    "code": i.get("code"),
                    "unit": i.get("unit"),
                    "type": i.get("type"),
                    "delivery_time": i.get("delivery_time"),
                    "delivery_time_raw": i.get("delivery_time_raw"),
                    "concierge": i.get("concierge"),
                    "collected_at": i.get("collected_at"),
                }
                for i in collected
            ],
            ATTR_UNIT: ", ".join(units),
            ATTR_EMAIL: self.coordinator.user_email(self._email_key),
            ATTR_ORG_ID: self.coordinator.user_org_id(self._email_key),
            ATTR_BASE_URL: self.coordinator.user_base_url(self._email_key),
            ATTR_LAST_UPDATED: state.get("last_updated"),
            ATTR_RETENTION_DAYS: self.coordinator.options.get(CONF_RETENTION_DAYS),
            "status_available": STATUS_AVAILABLE,
            "status_collected": STATUS_COLLECTED,
        }
