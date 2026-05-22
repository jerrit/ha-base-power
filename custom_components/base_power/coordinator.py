"""DataUpdateCoordinator for Base Power."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BasePowerAPI, BasePowerAPIError
from .auth import ClerkAuth, ClerkAuthError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class BasePowerCoordinator(DataUpdateCoordinator):
    """Coordinator that polls the Base Power API on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BasePowerAPI,
        auth: ClerkAuth,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.auth = auth

    async def _async_update_data(self) -> dict:
        """Fetch all data from the Base Power API concurrently."""
        try:
            results = await asyncio.gather(
                self.api.get_dashboard_root(),
                self.api.get_grid_status(),
                self.api.get_recent_usage(),
                return_exceptions=True,
            )

            dashboard, grid, usage = results
            data: dict = {}

            if isinstance(dashboard, Exception):
                _LOGGER.warning("Dashboard fetch failed: %s", dashboard)
            else:
                data["dashboard"] = dashboard

            if isinstance(grid, Exception):
                _LOGGER.debug("Grid status fetch failed (may be normal for new installs): %s", grid)
            else:
                data["grid"] = grid

            if isinstance(usage, Exception):
                _LOGGER.warning("Recent usage fetch failed: %s", usage)
            else:
                data["usage"] = usage

            if not data:
                raise UpdateFailed("All Base Power API calls failed")

            return data

        except ClerkAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BasePowerAPIError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
