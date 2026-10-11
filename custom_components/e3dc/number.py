"""Support for E3/DC setpoints as numbers."""

from dataclasses import dataclass
from typing import override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import E3dcConfigEntry, E3dcRuntimeData
from .e3dc_ess_modbus import Setpoint
from .entity import E3dcEntity, E3dcEntityDescription

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class E3dcSetpointNumberDescription(NumberEntityDescription, E3dcEntityDescription):
    """Describe a setpoint the integration holds once it is set."""

    setpoint: Setpoint


SETPOINT_NUMBERS: tuple[E3dcSetpointNumberDescription, ...] = (
    E3dcSetpointNumberDescription(
        key="battery_power_setpoint",
        translation_key="battery_power_setpoint",
        component="battery_control",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=-50000,
        native_max_value=50000,
        native_step=100,
        mode=NumberMode.BOX,
        setpoint=Setpoint.BATTERY_POWER,
    ),
    E3dcSetpointNumberDescription(
        key="feed_in_limit_setpoint",
        translation_key="feed_in_limit_setpoint",
        component="grid_control",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=100000,
        native_step=100,
        mode=NumberMode.BOX,
        setpoint=Setpoint.FEED_IN_LIMIT,
    ),
    E3dcSetpointNumberDescription(
        key="active_power_limit_setpoint",
        translation_key="active_power_limit_setpoint",
        component="grid_control",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        mode=NumberMode.BOX,
        setpoint=Setpoint.ACTIVE_POWER_LIMIT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: E3dcConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the E3/DC number platform."""
    runtime_data = entry.runtime_data
    served = runtime_data.served_components
    async_add_entities(
        E3dcSetpointNumber(runtime_data, description)
        for description in SETPOINT_NUMBERS
        if description.component in served
    )


class E3dcSetpointNumber(E3dcEntity, RestoreNumber):
    """A setpoint: setting it writes it and starts holding it.

    It shows the last value set, held or not, and restores it after a
    restart without writing it, so the control switch can hold it again.
    """

    entity_description: E3dcSetpointNumberDescription

    def __init__(
        self,
        runtime_data: E3dcRuntimeData,
        entity_description: E3dcSetpointNumberDescription,
    ) -> None:
        """Initialize the number."""
        super().__init__(runtime_data, entity_description)
        self._holds = runtime_data.holds

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._holds.async_add_listener(self.async_write_ha_state))
        setpoint = self.entity_description.setpoint
        last = await self.async_get_last_number_data()
        # A value set while the restore was loading wins over the restored one
        if (
            last is not None
            and last.native_value is not None
            and self._holds.requested(setpoint) is None
        ):
            self._holds.remember(setpoint, last.native_value)

    @property
    @override
    def available(self) -> bool:
        """Always, because holding a setpoint doesn't depend on the last poll."""
        return True

    @property
    @override
    def native_value(self) -> float | None:
        return self._holds.requested(self.entity_description.setpoint)

    @override
    async def async_set_native_value(self, value: float) -> None:
        await self._holds.async_start(self.entity_description.setpoint, value)
