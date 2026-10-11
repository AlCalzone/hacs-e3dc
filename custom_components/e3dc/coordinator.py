"""Data update coordinator for E3/DC devices."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusError
from propcache.api import cached_property

from .const import DEFAULT_NAME, DOMAIN
from .e3dc_ess_modbus import E3dcPowerStation, NotAnE3dcError, UpdateReport

_LOGGER = logging.getLogger(__name__)


class E3dcDataUpdateCoordinator(DataUpdateCoordinator[UpdateReport]):
    """Run one of the power station's update methods on its own interval."""

    config_entry: E3dcConfigEntry
    device: E3dcPowerStation

    def __init__(
        self,
        hass: HomeAssistant,
        entry: E3dcConfigEntry,
        device: E3dcPowerStation,
        poll: Callable[[], Awaitable[UpdateReport]],
        interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=interval,
        )
        self.device = device
        self._poll = poll
        self._failing: set[str] = set()

    @cached_property
    def device_info(self) -> dr.DeviceInfo:
        """Return device information."""
        identity = self.device.identity
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or "")},
            manufacturer=identity.manufacturer or DEFAULT_NAME,
            model=identity.model or None,
            serial_number=identity.serial_number or None,
            sw_version=identity.firmware_release or None,
        )

    @override
    async def _async_update_data(self) -> UpdateReport:
        try:
            report = await self._poll()
        except NotAnE3dcError as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="not_e3dc",
            ) from err
        except ModbusError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="modbus_error",
            ) from err
        if report.failed and not report.updated:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="no_component_answered",
            ) from ExceptionGroup(
                "all components failed to refresh", list(report.failed.values())
            )

        for name, cause in report.failed.items():
            if name not in self._failing:
                _LOGGER.warning(
                    "%s: %s failed to refresh and is unavailable: %s",
                    self.name,
                    name,
                    cause,
                )
        for name in self._failing & report.updated:
            _LOGGER.info("%s: %s is available again", self.name, name)
        self._failing = (self._failing - report.updated) | set(report.failed)
        return report


@dataclass
class E3dcRuntimeData:
    """Class to hold runtime data."""

    readings: E3dcDataUpdateCoordinator
    settings: E3dcDataUpdateCoordinator
    station_device_id: str
    # The wallboxes each platform has added entities for, keyed by platform.
    wired_wallboxes: dict[str, set[int]] = field(default_factory=dict)

    @property
    def device(self) -> E3dcPowerStation:
        """The power station both coordinators poll."""
        return self.readings.device

    @property
    def served_components(self) -> frozenset[str]:
        """Component names this power station polls, answered or not."""
        return frozenset(self.device.readings_components) | frozenset(
            self.device.settings_components
        )

    def coordinator_for(self, component: str) -> E3dcDataUpdateCoordinator:
        """Which coordinator owns a given component's data."""
        if component in self.device.settings_components:
            return self.settings
        return self.readings


type E3dcConfigEntry = ConfigEntry[E3dcRuntimeData]
