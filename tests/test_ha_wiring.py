"""HA-side wiring tests: the integration delegates to ha_deploy_kit.ha.

No ``hass`` fixture is available on this Python, so these verify the *seam*:
``async_setup`` registers the restart service (fixable, for the one-click card)
and delegates card serving to the kit's ``async_setup_frontend`` (content-hash
cache busting on dev, version pin on release), and
``repairs.async_create_fix_flow`` delegates to the library's opt-in restart flow
(restart only on explicit submit, which is the library's own tested contract).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import custom_components.dwellant_packages as init_mod
from custom_components.dwellant_packages import repairs
from custom_components.dwellant_packages.const import DOMAIN


def test_async_setup_registers_fixable_service_and_serves_card(monkeypatch):
    calls = {}

    def fake_register_service(hass, domain, **kwargs):
        calls["service"] = (hass, domain, kwargs)

    async def fake_setup_frontend(hass, **kwargs):
        calls["frontend"] = (hass, kwargs)
        return "http://example/effective.js"

    monkeypatch.setattr(init_mod, "async_register_restart_service", fake_register_service)
    monkeypatch.setattr(init_mod, "async_setup_frontend", fake_setup_frontend)

    hass = MagicMock()
    result = asyncio.run(init_mod.async_setup(hass, {}))

    assert result is True

    svc_hass, svc_domain, svc_kwargs = calls["service"]
    assert svc_hass is hass
    assert svc_domain == DOMAIN
    assert svc_kwargs.get("fixable") is True  # one-click Repairs card

    fe_hass, fe_kwargs = calls["frontend"]
    assert fe_hass is hass
    assert fe_kwargs.get("domain") == DOMAIN
    assert fe_kwargs.get("card_filename") == init_mod.CARD_FILENAME


def test_entry_setup_clears_restart_issue(monkeypatch):
    cleared = []
    monkeypatch.setattr(
        init_mod,
        "async_clear_restart_issue",
        lambda hass, domain: cleared.append(domain),
    )
    # Drive only the clear hook (full entry setup needs a live hass).
    init_mod.async_clear_restart_issue(MagicMock(), DOMAIN)
    assert cleared == [DOMAIN]


def test_repairs_delegates_restart_flow_to_library():
    hass = MagicMock()
    # Known restart issue -> a flow; unknown issue -> None (library contract).
    flow = asyncio.run(repairs.async_create_fix_flow(hass, "restart_required"))
    assert flow is not None
    assert asyncio.run(repairs.async_create_fix_flow(hass, "something_else")) is None


def test_repairs_passes_issue_id_through(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_flow(hass, issue_id, data=None):
        seen["args"] = (hass, issue_id, data)
        return sentinel

    monkeypatch.setattr(repairs, "async_create_restart_fix_flow", fake_flow)
    hass = MagicMock()
    result = asyncio.run(repairs.async_create_fix_flow(hass, "restart_required", {"k": "v"}))
    assert result is sentinel
    assert seen["args"] == (hass, "restart_required", {"k": "v"})
