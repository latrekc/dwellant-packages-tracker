"""Repairs platform: one-click restart-required fix flow.

After a deploy copies new backend files, Home Assistant keeps running the OLD
code until restart. The deploy raises a "Restart required" Repairs issue via the
shared service; clicking it opens a confirm form and restarts HA ONLY on submit.
Both the issue and the fix flow come from ``ha_deploy_kit.ha``.
"""

from __future__ import annotations

from typing import Any

from ha_deploy_kit.ha import async_create_restart_fix_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None = None,
) -> RepairsFlow | None:
    """Return the library's opt-in restart fix flow for the restart issue."""
    return async_create_restart_fix_flow(hass, issue_id, data)
