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
from .coordinator import E3dcConfigEntry
from .e3dc_ess_modbus import E3dcPowerStation
from .entity import E3dcEntity, E3dcEntityDescription, async_add_wallbox_entities

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
            value_fn=lambda device: (
                None
                if (aborted := device.wallboxes.slots[number].charging_aborted) is None
                else not aborted
            ),
            write_fn=lambda device, value: device.wallboxes.slots[
                number
            ].async_write_charging(value),
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: E3dcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the E3/DC switch platform."""
    runtime_data = entry.runtime_data
    async_add_wallbox_entities(
        entry,
        Platform.SWITCH,
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
        except ModbusError as err:
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
