"""Integrate E3/DC home power stations into Home Assistant."""

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .config_flow import create_modbus_params
from .const import DOMAIN, SCAN_INTERVAL, SETTINGS_SCAN_INTERVAL, UNIT_ID
from .coordinator import E3dcConfigEntry, E3dcDataUpdateCoordinator, E3dcRuntimeData
from .e3dc_ess_modbus import E3dcPowerStation
from .e3dc_ess_modbus.wallbox import WALLBOX_COUNT
from .entity import part_identifier
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the E3/DC integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: E3dcConfigEntry) -> bool:
    """Set up E3/DC from a config entry."""
    unit = async_get_unit(hass, entry, create_modbus_params(entry.data), UNIT_ID)
    device = E3dcPowerStation(unit)

    readings = E3dcDataUpdateCoordinator(
        hass,
        entry,
        device,
        device.async_update_readings,
        timedelta(seconds=SCAN_INTERVAL),
    )
    settings = E3dcDataUpdateCoordinator(
        hass,
        entry,
        device,
        device.async_update_settings,
        timedelta(seconds=SETTINGS_SCAN_INTERVAL),
    )
    # The first readings poll also reads the identity and settles which
    # blocks this firmware serves.
    await readings.async_config_entry_first_refresh()
    await settings.async_refresh()

    # Register the power station first, because each part links to its id.
    station = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **readings.device_info
    )
    entry.runtime_data = E3dcRuntimeData(readings, settings, station.id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: E3dcConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Allow removing a wallbox the power station no longer reports."""
    serial = config_entry.unique_id
    if TYPE_CHECKING:
        assert serial is not None
    runtime_data = (
        config_entry.runtime_data
        if config_entry.state is ConfigEntryState.LOADED
        else None
    )
    wallboxes = {
        part_identifier(serial, ("wallbox", number)): number
        for number in range(WALLBOX_COUNT)
    }
    removed: set[int] = set()
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if (number := wallboxes.get(identifier)) is None:
            return False
        if runtime_data is not None and _wallbox_reported(runtime_data, number):
            return False
        removed.add(number)

    if runtime_data is not None:
        for wired in runtime_data.wired_wallboxes.values():
            wired -= removed
    return True


def _wallbox_reported(runtime_data: E3dcRuntimeData, number: int) -> bool:
    """Whether the power station still reports a wallbox in this slot."""
    return any(slot == number for slot, _ in runtime_data.device.wallboxes.available)


async def async_unload_entry(hass: HomeAssistant, entry: E3dcConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
