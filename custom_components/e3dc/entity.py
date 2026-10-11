"""Base entity for E3/DC devices."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import override

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import E3dcConfigEntry, E3dcDataUpdateCoordinator, E3dcRuntimeData

# A part of the power station, as (device translation key, number).
type E3dcPart = tuple[str, int]


@dataclass(frozen=True, kw_only=True)
class E3dcEntityDescription(EntityDescription):
    """Describe an E3/DC entity."""

    component: str  # attribute name on E3dcPowerStation, e.g. 'power'
    part: E3dcPart | None = None  # None keeps the entity on the power station


def part_identifier(serial: str, part: E3dcPart) -> str:
    """The device registry identifier of a part of the power station."""
    kind, number = part
    return f"{serial}_{kind}_{number}"


class E3dcEntity(CoordinatorEntity[E3dcDataUpdateCoordinator]):
    """Defines a base E3/DC entity."""

    _attr_has_entity_name = True
    entity_description: E3dcEntityDescription

    def __init__(
        self,
        runtime_data: E3dcRuntimeData,
        entity_description: E3dcEntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(runtime_data.coordinator_for(entity_description.component))
        self.entity_description = entity_description
        serial = self.coordinator.config_entry.unique_id
        assert serial is not None
        self._attr_unique_id = f"{serial}_{entity_description.key}"
        self._attr_device_info = self._device_info(runtime_data, serial)

    def _device_info(self, runtime_data: E3dcRuntimeData, serial: str) -> dr.DeviceInfo:
        """Place the entity on the power station, or on the part it measures."""
        if (part := self.entity_description.part) is None:
            return self.coordinator.device_info
        kind, number = part
        return dr.DeviceInfo(
            identifiers={(DOMAIN, part_identifier(serial, part))},
            via_device_id=runtime_data.station_device_id,
            translation_key=kind,
            translation_placeholders={"number": str(number)},
        )

    @property
    @override
    def available(self) -> bool:
        """Whether this entity's component answered the most recent poll."""
        if not super().available:
            return False
        return self.entity_description.component not in self.coordinator.data.failed


@callback
def async_add_wallbox_entities(
    entry: E3dcConfigEntry,
    platform: str,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entities_for: Callable[[int], Iterable[Entity]],
) -> None:
    """Add a wallbox's entities the first time the power station reports it."""
    runtime_data = entry.runtime_data
    wired = runtime_data.wired_wallboxes.setdefault(platform, set())

    @callback
    def _async_add_new() -> None:
        new = [
            number
            for number, _ in runtime_data.device.wallboxes.available
            if number not in wired
        ]
        if not new:
            return
        wired.update(new)
        async_add_entities(entity for number in new for entity in entities_for(number))

    _async_add_new()
    entry.async_on_unload(runtime_data.readings.async_add_listener(_async_add_new))
