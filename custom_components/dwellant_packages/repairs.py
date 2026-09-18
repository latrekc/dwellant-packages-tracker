"""Repairs platform: restart-required issue with a one-click fix (HACS model).

After a manual deploy (or HACS update) copies new backend files, HA keeps
running the OLD code until restart. The integration raises a Repairs issue so
the notice waits in Settings -> Repairs instead of scrolling past in a
terminal. Clicking Submit restarts HA — exactly like HACS does.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN

ISSUE_ID_RESTART_REQUIRED = "restart_required"


class RestartRequiredFixFlow(RepairsFlow):
    """Confirm-and-restart fix flow for the restart-required issue."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show confirm form; restart on submit."""
        if user_input is not None:
            await self.hass.services.async_call("homeassistant", "restart")
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm_restart")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None = None,
) -> RepairsFlow | None:
    """Return the fix flow for known issues."""
    if issue_id == ISSUE_ID_RESTART_REQUIRED:
        return RestartRequiredFixFlow()
    return None
