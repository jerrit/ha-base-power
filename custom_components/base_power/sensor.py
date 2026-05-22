"""Sensor entities for Base Power."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACCOUNT_STATUS, BATTERY_STATUS, DOMAIN, SERVICE_STATE
from .coordinator import BasePowerCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class BasePowerSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extractor."""

    value_fn: Callable[[dict], Any]


# ---------------------------------------------------------------------------
# Sensor definitions
# ---------------------------------------------------------------------------

SENSOR_DESCRIPTIONS: tuple[BasePowerSensorDescription, ...] = (
    # --- Energy (current 15-min interval) ---
    BasePowerSensorDescription(
        key="current_interval_kwh",
        name="Current Interval Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.get("usage", {}).get("current_interval_kwh"),
    ),
    # --- Power (derived: kWh/15min → Watts) ---
    BasePowerSensorDescription(
        key="current_power_watts",
        name="Current Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:flash",
        value_fn=lambda d: d.get("usage", {}).get("current_power_watts"),
    ),
    # --- Daily totals ---
    BasePowerSensorDescription(
        key="daily_total_kwh",
        name="Daily Total Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:counter",
        value_fn=lambda d: d.get("usage", {}).get("daily_total_kwh"),
    ),
    BasePowerSensorDescription(
        key="daily_peak_kwh",
        name="Daily Peak Interval",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:chart-bar",
        value_fn=lambda d: d.get("usage", {}).get("daily_peak_kwh"),
    ),
    BasePowerSensorDescription(
        key="intervals_today",
        name="Intervals Today",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="intervals",
        icon="mdi:calendar-clock",
        value_fn=lambda d: d.get("usage", {}).get("intervals_today"),
    ),
    # --- Dashboard / battery ---
    BasePowerSensorDescription(
        key="battery_status",
        name="Battery Status",
        icon="mdi:battery-heart-variant",
        value_fn=lambda d: d.get("dashboard", {}).get("battery_status_name"),
    ),
    BasePowerSensorDescription(
        key="service_state",
        name="Service State",
        icon="mdi:home-lightning-bolt",
        value_fn=lambda d: d.get("dashboard", {}).get("service_state_name"),
    ),
    # --- Grid energy flows (populated when system matures) ---
    BasePowerSensorDescription(
        key="grid_to_home_kwh",
        name="Grid to Home Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
        value_fn=lambda d: d.get("dashboard", {}).get("grid_to_home_kwh"),
    ),
    BasePowerSensorDescription(
        key="solar_to_home_kwh",
        name="Solar to Home Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-power",
        value_fn=lambda d: d.get("dashboard", {}).get("solar_to_home_kwh"),
    ),
    BasePowerSensorDescription(
        key="energy_to_home_kwh",
        name="Total Energy to Home",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:home-import-outline",
        value_fn=lambda d: d.get("dashboard", {}).get("energy_to_home_kwh"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power sensors."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BasePowerSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class BasePowerSensor(CoordinatorEntity[BasePowerCoordinator], SensorEntity):
    """A sensor entity backed by the Base Power coordinator."""

    entity_description: BasePowerSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        description: BasePowerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data.get("service_location_id", entry.entry_id))},
        name=entry.title,
        manufacturer="Base Power",
        model="Home Battery System",
        configuration_url="https://basepowercompany.com",
    )
