"""Support for E3/DC switches."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from modbus_connection import ModbusError

from .const import DOMAIN
from .coordinator import E3dcConfigEntry, E3dcRuntimeData
from .e3dc_ess_modbus import E3dcError, E3dcPowerStation, Setpoint
from .entity import (
    E3dcEntity,
    E3dcEntityDescription,
    async_add_part_entities,
    present_wallboxes,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class E3dcSwitchEntityDescription(SwitchEntityDescription, E3dcEntityDescription):
    """Describe an E3/DC switch entity."""

    value_fn: Callable[[E3dcPowerStation], bool | None]
    write_fn: Callable[[E3dcPowerStation, bool], Awaitable[None]]


def wallbox_switches(number: int) -> list[E3dcSwitchEntityDescription]:
    """The switches of wallbox slot ``number``."""
    part = ("wallbox", number)
    return [
        E3dcSwitchEntityDescription(
            key=f"wallbox_{number}_charging_enabled",
            translation_key="charging_enabled",
            component="wallboxes",
            part=part,
            value_fn=lambda device: device.wallboxes.slots[number].charging_allowed,
            write_fn=lambda device, value: device.wallboxes.slots[
                number
            ].async_write_charging_allowed(value),
        ),
        E3dcSwitchEntityDescription(
            key=f"wallbox_{number}_solar_mode",
            translation_key="solar_mode",
            component="wallboxes",
            part=part,
            value_fn=lambda device: device.wallboxes.slots[number].solar_mode,
            write_fn=lambda device, value: device.wallboxes.slots[
                number
            ].async_write_solar_mode(value),
        ),
        E3dcSwitchEntityDescription(
            key=f"wallbox_{number}_one_phase",
            translation_key="one_phase",
            component="wallboxes",
            part=part,
            value_fn=lambda device: device.wallboxes.slots[number].one_phase,
            write_fn=lambda device, value: device.wallboxes.slots[
                number
            ].async_write_one_phase(value),
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class E3dcSetpointSwitchDescription(SwitchEntityDescription, E3dcEntityDescription):
    """Describe a switch that holds a setpoint or hands it back."""

    setpoint: Setpoint


SETPOINT_SWITCHES: tuple[E3dcSetpointSwitchDescription, ...] = (
    E3dcSetpointSwitchDescription(
        key="battery_power_control",
        translation_key="battery_power_control",
        component="battery_control",
        setpoint=Setpoint.BATTERY_POWER,
    ),
    E3dcSetpointSwitchDescription(
        key="feed_in_limit_control",
        translation_key="feed_in_limit_control",
        component="grid_control",
        setpoint=Setpoint.FEED_IN_LIMIT,
    ),
    E3dcSetpointSwitchDescription(
        key="active_power_limit_control",
        translation_key="active_power_limit_control",
        component="grid_control",
        setpoint=Setpoint.ACTIVE_POWER_LIMIT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: E3dcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the E3/DC switch platform."""
    runtime_data = entry.runtime_data
    served = runtime_data.served_components
    async_add_entities(
        E3dcSetpointSwitch(runtime_data, description)
        for description in SETPOINT_SWITCHES
        if description.component in served
    )
    async_add_part_entities(
        entry,
        Platform.SWITCH,
        "wallbox",
        present_wallboxes,
        async_add_entities,
        lambda number: (
            E3dcSwitch(runtime_data, description)
            for description in wallbox_switches(number)
        ),
    )


class E3dcSwitch(E3dcEntity, SwitchEntity):
    """Defines an E3/DC switch entity."""

    entity_description: E3dcSwitchEntityDescription

    @property
    @override
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.device)

    async def _async_write(self, value: bool) -> None:
        """Write the switch state to the device."""
        try:
            await self.entity_description.write_fn(self.coordinator.device, value)
        except (ModbusError, E3dcError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
            ) from err
        await self.coordinator.async_request_refresh()

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)


class E3dcSetpointSwitch(E3dcEntity, SwitchEntity):
    """On while the integration holds a setpoint.

    It is unknown while a rewrite or a hand-back fails, because the device
    may hold either state then.
    """

    entity_description: E3dcSetpointSwitchDescription

    def __init__(
        self,
        runtime_data: E3dcRuntimeData,
        entity_description: E3dcSetpointSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(runtime_data, entity_description)
        self._holds = runtime_data.holds

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._holds.async_add_listener(self.async_write_ha_state))

    @property
    @override
    def available(self) -> bool:
        """Always, because holding a setpoint doesn't depend on the last poll."""
        return True

    @property
    @override
    def is_on(self) -> bool | None:
        if (hold := self._holds.get(self.entity_description.setpoint)) is None:
            return False
        return True if hold.confirmed else None

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._holds.async_resume(self.entity_description.setpoint)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._holds.async_stop(self.entity_description.setpoint)
