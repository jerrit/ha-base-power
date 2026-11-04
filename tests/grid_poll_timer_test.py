"""The 60-second grid poll must not delay the 15-minute usage refresh (issue #13).

Needs Home Assistant installed. Skipped otherwise.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import frame  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _load_coordinator_module():
    components = sys.modules.get("custom_components")
    if components is None or not hasattr(components, "__path__"):
        components = types.ModuleType("custom_components")
        components.__path__ = [str(_ROOT / "custom_components")]
        sys.modules["custom_components"] = components
    return importlib.import_module("custom_components.base_power.coordinator")


async def _scheduled_refresh_after_grid_polls(polls: int) -> tuple[bool, bool]:
    """Return (refresh still scheduled, listeners notified) after some grid polls."""
    module = _load_coordinator_module()
    hass = HomeAssistant(str(_ROOT))
    frame.async_setup(hass)
    coordinator = module.BasePowerCoordinator.__new__(module.BasePowerCoordinator)
    module.DataUpdateCoordinator.__init__(
        coordinator,
        hass,
        module._LOGGER,
        config_entry=None,
        name="Base Power",
        update_interval=module.timedelta(seconds=module.SCAN_INTERVAL_SECONDS),
    )
    coordinator._poll = types.SimpleNamespace(numeric_id=123, slow_due=lambda *a, **k: False)
    coordinator.data = {"seed": True}
    coordinator._compose = lambda: {"grid": "fresh"}

    async def no_auth() -> None:
        return None

    async def fake_grid(numeric_id):
        return {"ok": True}

    coordinator._async_ensure_jwt = no_auth
    coordinator._async_fetch_grid = fake_grid

    notified = []
    unsub = coordinator.async_add_listener(lambda: notified.append(1))
    try:
        first_timer = coordinator._unsub_refresh
        assert first_timer is not None
        for _ in range(polls):
            await coordinator.async_poll_grid_status()
        # The usage refresh timer must be the same one, not restarted.
        return coordinator._unsub_refresh is first_timer, len(notified) == polls
    finally:
        unsub()
        await hass.async_stop(force=True)


def test_grid_poll_keeps_the_usage_refresh_timer():
    same_timer, notified = asyncio.run(_scheduled_refresh_after_grid_polls(5))
    assert notified, "grid poll should update sensors"
    assert same_timer, "grid poll restarted the 15-minute refresh timer"
