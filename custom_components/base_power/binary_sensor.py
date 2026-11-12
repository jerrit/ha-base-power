"""Binary sensor entities for Base Power integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BasePowerCoordinator
from .entity import BasePowerEntity

Data = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class BasePowerBinarySensorDescription(BinarySensorEntityDescription):
    """One Base Power binary sensor. ``key`` is the end of its unique id."""

    is_on_fn: Callable[[Data], bool | None]
    attributes_fn: Callable[[Data], dict[str, Any]] | None = None


def _section(data: Data, name: str) -> Data:
    return data.get(name) or {}


def _battery_connected(data: Data) -> bool | None:
    connected = _section(data, "grid").get("gateway_connected")
    return None if connected is None else bool(connected)


def _grid_power(data: Data) -> bool | None:
    """True when the grid is up. Unknown when this poll has no grid telemetry.

    Guessing "on" would report a false all-clear during an outage.
    """
    grid = _section(data, "grid")
    if not grid.get("available"):
        return None
    return grid.get("grid_is_up")


def _grid_power_attributes(data: Data) -> dict[str, Any]:
    """What decides Grid Power: voltage, Base's outage flag, and the battery state.

    An off-grid battery_state turns Grid Power off even when voltage and
    active_outage still look normal.
    """
    grid = _section(data, "grid")
    attributes: dict[str, Any] = {}
    if grid.get("voltage_volts") is not None:
        attributes["voltage_volts"] = round(float(grid["voltage_volts"]), 1)
    if grid.get("is_active_outage") is not None:
        attributes["active_outage"] = bool(grid["is_active_outage"])
    if grid.get("voltage_source") is not None:
        attributes["voltage_source"] = grid["voltage_source"]
    if grid.get("battery_state") is not None:
        attributes["battery_state"] = grid["battery_state"]
    return attributes


def _grid_support_attributes(data: Data) -> dict[str, Any]:
    """What turned Grid Support on, and the battery's live output."""
    attributes: dict[str, Any] = {}
    source = _section(data, "derived").get("grid_support_source")
    if source:
        attributes["detected_by"] = source
    storage_kw = _section(data, "grid").get("power_from_storage_kw")
    if isinstance(storage_kw, (int, float)) and not isinstance(storage_kw, bool):
        attributes["battery_output_kw"] = round(storage_kw, 3)
    return attributes


BINARY_SENSORS: tuple[BasePowerBinarySensorDescription, ...] = (
    BasePowerBinarySensorDescription(
        key="has_solar",
        name="Solar Connected",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:solar-power",
        is_on_fn=lambda data: bool(_section(data, "dashboard").get("has_solar", False)),
    ),
    BasePowerBinarySensorDescription(
        key="battery_connected",
        name="Battery Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:wifi",
        is_on_fn=_battery_connected,
    ),
    # On means the grid is up, off means an outage.
    BasePowerBinarySensorDescription(
        key="grid_power",
        name="Grid Power",
        device_class=BinarySensorDeviceClass.POWER,
        icon="mdi:transmission-tower",
        is_on_fn=_grid_power,
        attributes_fn=_grid_power_attributes,
    ),
    # On after the charge rose, off after it fell or when the battery is
    # full. Unknown until the charge has moved once.
    BasePowerBinarySensorDescription(
        key="battery_charging",
        name="Battery Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        icon="mdi:battery-charging",
        is_on_fn=lambda data: _section(data, "derived").get("battery_charging"),
    ),
    # On while Base is using the battery to support the grid: its event
    # list shows an event now, or the grid is up and the battery is
    # powering the home. The live check runs every 60 seconds. The event
    # list refreshes every 15 minutes and can lag.
    BasePowerBinarySensorDescription(
        key="grid_support",
        name="Grid Support",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:transmission-tower-import",
        is_on_fn=lambda data: _section(data, "derived").get("grid_support_active"),
        attributes_fn=_grid_support_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power binary sensors from a config entry."""
    coordinator: BasePowerCoordinator = entry.runtime_data
    async_add_entities(
        BasePowerBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class BasePowerBinarySensor(BasePowerEntity, BinarySensorEntity):
    """A Base Power binary sensor described by one row of BINARY_SENSORS."""

    entity_description: BasePowerBinarySensorDescription

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        description: BasePowerBinarySensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state, or None (unknown) before the first data."""
        if not self.coordinator.data:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the extra attributes, when this sensor has any."""
        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is None or not self.coordinator.data:
            return None
        return attributes_fn(self.coordinator.data)
