"""Integrate E3/DC home power stations into Home Assistant."""

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .config_flow import create_modbus_params
from .const import DOMAIN, SCAN_INTERVAL, SETTINGS_SCAN_INTERVAL, UNIT_ID
from .coordinator import E3dcConfigEntry, E3dcDataUpdateCoordinator, E3dcRuntimeData
from .e3dc_ess_modbus import E3dcPowerStation
from .e3dc_ess_modbus.inverter import INVERTER_COUNT
from .e3dc_ess_modbus.meters import POWER_METER_COUNT
from .e3dc_ess_modbus.wallbox import WALLBOX_COUNT
from .entity import part_identifier, present_meters, present_wallboxes
from .holds import E3dcHolds

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

_PART_COUNTS = {
    "wallbox": WALLBOX_COUNT,
    "power_meter": POWER_METER_COUNT,
    "inverter": INVERTER_COUNT,
}


async def async_setup_entry(hass: HomeAssistant, entry: E3dcConfigEntry) -> bool:
    """Set up E3/DC from a config entry."""
    try:
        unit = async_get_unit(hass, entry, create_modbus_params(entry.data), UNIT_ID)
    except HomeAssistantError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="link_settings_in_use",
            translation_placeholders={"error": str(err)},
        ) from err
    device = E3dcPowerStation(unit)
    entry.async_on_unload(device.close)

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
    if device.serial_number != entry.unique_id:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="serial_mismatch",
            translation_placeholders={
                "expected": str(entry.unique_id),
                "found": str(device.serial_number),
            },
        )
    await settings.async_refresh()

    # Register the power station first, because each part links to its id.
    station = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **readings.device_info
    )

    holds = E3dcHolds(hass, entry, device)
    entry.runtime_data = E3dcRuntimeData(readings, settings, station.id, holds)

    # Home Assistant doesn't unload entries when it stops
    async def _async_release_on_stop(event: Event) -> None:
        await holds.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_release_on_stop)
    )
    _async_remove_stale_devices(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _reported_parts(runtime_data: E3dcRuntimeData) -> dict[str, set[int]] | None:
    """The parts the power station reports, or ``None`` if a block didn't answer.

    An inverter slot counts while it answers, even while its inverter is off.
    """
    if {"wallboxes", "power_meters"} - runtime_data.readings.data.updated:
        return None
    device = runtime_data.device
    return {
        "wallbox": set(present_wallboxes(device)),
        "power_meter": set(present_meters(device)),
        "inverter": set(device.inverters or ()),
    }


def _part_of(serial: str, identifier: str) -> tuple[str, int] | None:
    """The part a device identifier names, if it names one."""
    for kind, count in _PART_COUNTS.items():
        for number in range(count):
            if identifier == part_identifier(serial, (kind, number)):
                return kind, number
    return None


@callback
def _async_remove_stale_devices(hass: HomeAssistant, entry: E3dcConfigEntry) -> None:
    """Remove the devices of power meters that are no longer configured.

    A wallbox that is switched off, or an inverter slot that times out once,
    looks the same as one that is gone. Those are left to be removed by hand.
    """
    serial = entry.unique_id
    assert serial is not None
    if (reported := _reported_parts(entry.runtime_data)) is None:
        return
    registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, identifier in device_entry.identifiers:
            if domain != DOMAIN or (part := _part_of(serial, identifier)) is None:
                continue
            kind, number = part
            if kind == "power_meter" and number not in reported[kind]:
                registry.async_remove_device(device_entry.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: E3dcConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Allow removing a part the power station no longer reports."""
    serial = config_entry.unique_id
    if TYPE_CHECKING:
        assert serial is not None
    runtime_data = (
        config_entry.runtime_data
        if config_entry.state is ConfigEntryState.LOADED
        else None
    )
    reported = _reported_parts(runtime_data) if runtime_data is not None else {}
    if reported is None:
        return False
    removed: list[tuple[str, int]] = []
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if (part := _part_of(serial, identifier)) is None:
            return False
        kind, number = part
        if number in reported.get(kind, ()):
            return False
        removed.append(part)

    if runtime_data is not None:
        for (kind, _), wired in runtime_data.wired_parts.items():
            wired -= {number for part_kind, number in removed if part_kind == kind}
    return True


async def async_unload_entry(hass: HomeAssistant, entry: E3dcConfigEntry) -> bool:
    """Unload a config entry and hand every held setpoint back."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.holds.async_close()
    return True
