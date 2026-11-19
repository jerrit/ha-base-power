"""Shared base for Base Power entities."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERVICE_LOCATION_ID, CONF_SITE_LABEL, DOMAIN
from .coordinator import BasePowerCoordinator

type BasePowerConfigEntry = ConfigEntry[BasePowerCoordinator]

DEFAULT_MODEL = "Home Battery System"


def device_model(data: dict[str, Any] | None) -> str:
    """The battery SKU, else the battery models from the site, else a generic name."""
    dashboard = (data or {}).get("dashboard") or {}
    sku = dashboard.get("hardware_sku")
    if sku and sku != "NO_BATTERY":
        return sku
    models = [
        " ".join(p for p in (b.get("generation"), b.get("variant")) if p)
        for b in dashboard.get("batteries") or []
    ]
    models = [m for m in models if m]
    return " + ".join(models) if models else DEFAULT_MODEL


def device_info(
    site_id: str, label: str | None = None, model: str = DEFAULT_MODEL
) -> DeviceInfo:
    """The one device every entity of a site belongs to.

    Named after the site's street and city when setup found them.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, site_id)},
        name=f"Base Power {label}" if label else f"Base Power {site_id}",
        manufacturer="Base Power",
        model=model,
    )


class BasePowerEntity(CoordinatorEntity[BasePowerCoordinator]):
    """An entity of one Base Power site."""

    _attr_has_entity_name = True
    # True for entities fed by the 15-minute usage tier. They go
    # unavailable when that data stops updating, so an old value is not
    # shown as current.
    _usage_tier = False

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        unique_key: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        site_id = entry.data[CONF_SERVICE_LOCATION_ID]
        self._attr_unique_id = f"{site_id}_{unique_key}"
        self._attr_device_info = device_info(
            site_id,
            entry.data.get(CONF_SITE_LABEL) or None,
            device_model(coordinator.data),
        )

    @property
    def available(self) -> bool:
        """Unavailable when the coordinator failed or this entity's data is stale."""
        if self._usage_tier and self.coordinator.usage_stale:
            return False
        return super().available
