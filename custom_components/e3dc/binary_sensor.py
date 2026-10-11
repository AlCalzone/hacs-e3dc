"""Support for E3/DC binary sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import E3dcConfigEntry
from .e3dc_ess_modbus import E3dcPowerStation, EmsStatus
from .entity import (
    E3dcEntity,
    E3dcEntityDescription,
    async_add_part_entities,
    present_wallboxes,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class E3dcBinarySensorDescription(E3dcEntityDescription, BinarySensorEntityDescription):
    """Describe an E3/DC binary sensor."""

    value_fn: Callable[[E3dcPowerStation], bool | None]


def _ems_flag(
    key: str, flag: EmsStatus, *, enabled: bool = True
) -> E3dcBinarySensorDescription:
    """A binary sensor for one bit of the EMS status register."""

    def _value(device: E3dcPowerStation) -> bool | None:
        status = device.power.ems_status
        return None if status is None else flag in status

    return E3dcBinarySensorDescription(
        key=key,
        translation_key=key,
        component="power",
        entity_registry_enabled_default=enabled,
        value_fn=_value,
    )


STATION_BINARY_SENSORS: tuple[E3dcBinarySensorDescription, ...] = (
    _ems_flag("charging_locked", EmsStatus.CHARGING_LOCKED),
    _ems_flag("discharging_locked", EmsStatus.DISCHARGING_LOCKED),
    _ems_flag("emergency_power_ready", EmsStatus.EMERGENCY_POWER_READY),
    _ems_flag("weather_based_charging", EmsStatus.WEATHER_BASED_CHARGING),
    _ems_flag("feed_in_limited", EmsStatus.FEED_IN_LIMITED),
    _ems_flag("charge_lock_time", EmsStatus.CHARGE_LOCK_TIME, enabled=False),
    _ems_flag("discharge_lock_time", EmsStatus.DISCHARGE_LOCK_TIME, enabled=False),
    E3dcBinarySensorDescription(
        key="power_output_ready",
        translation_key="power_output_ready",
        component="grid_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: (
            status.power_output_ready
            if (status := device.grid_status) is not None
            else None
        ),
    ),
)


def wallbox_binary_sensors(number: int) -> list[E3dcBinarySensorDescription]:
    """The binary sensors of wallbox slot ``number``."""
    part = ("wallbox", number)
    return [
        E3dcBinarySensorDescription(
            key=f"wallbox_{number}_charging",
            component="wallboxes",
            part=part,
            device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
            value_fn=lambda device: device.wallboxes.slots[number].charging,
        ),
        E3dcBinarySensorDescription(
            key=f"wallbox_{number}_type2_plugged",
            translation_key="type2_plugged",
            component="wallboxes",
            part=part,
            device_class=BinarySensorDeviceClass.PLUG,
            value_fn=lambda device: device.wallboxes.slots[number].type2_plugged,
        ),
        E3dcBinarySensorDescription(
            key=f"wallbox_{number}_type2_locked",
            translation_key="type2_locked",
            component="wallboxes",
            part=part,
            entity_registry_enabled_default=False,
            value_fn=lambda device: device.wallboxes.slots[number].type2_locked,
        ),
        E3dcBinarySensorDescription(
            key=f"wallbox_{number}_schuko_plugged",
            translation_key="schuko_plugged",
            component="wallboxes",
            part=part,
            device_class=BinarySensorDeviceClass.PLUG,
            entity_registry_enabled_default=False,
            value_fn=lambda device: device.wallboxes.slots[number].schuko_plugged,
        ),
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: E3dcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the E3/DC binary sensor platform."""
    runtime_data = entry.runtime_data
    served = runtime_data.served_components
    async_add_entities(
        E3dcBinarySensor(runtime_data, description)
        for description in STATION_BINARY_SENSORS
        if description.component in served
    )

    async_add_part_entities(
        entry,
        Platform.BINARY_SENSOR,
        "wallbox",
        present_wallboxes,
        async_add_entities,
        lambda number: (
            E3dcBinarySensor(runtime_data, description)
            for description in wallbox_binary_sensors(number)
        ),
    )


class E3dcBinarySensor(E3dcEntity, BinarySensorEntity):
    """Defines an E3/DC binary sensor."""

    entity_description: E3dcBinarySensorDescription

    @property
    @override
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.device)
