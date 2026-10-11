"""Diagnostics support for E3/DC."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from modbus_connection import ModbusError

from .coordinator import E3dcConfigEntry
from .e3dc_ess_modbus import Setpoint
from .e3dc_ess_modbus.model import register

TO_REDACT = {"serial_number"}

_SERIAL_NUMBER_REGISTERS = range(register(40036), register(40052))


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: E3dcConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    device = runtime_data.device
    identity = device.identity
    raw = read_error = None
    try:
        raw = await device.async_read_raw()
    except ModbusError as err:
        read_error = type(err).__name__
    if raw is not None and (holding := raw.get("holding")) is not None:
        for address in _SERIAL_NUMBER_REGISTERS:
            holding.pop(address, None)

    return async_redact_data(
        {
            "manufacturer": identity.manufacturer,
            "model": identity.model,
            "serial_number": identity.serial_number,
            "firmware_release": identity.firmware_release,
            "modbus_version": identity.modbus_version,
            "register_count": identity.register_count,
            "readings_components": device.readings_components,
            "settings_components": device.settings_components,
            "inverters": sorted(device.inverters or ()),
            "wallboxes": list(device.wallboxes.configured),
            "coordinators": {
                name: {
                    "last_update_success": coordinator.last_update_success,
                    "last_exception": repr(coordinator.last_exception)
                    if coordinator.last_exception is not None
                    else None,
                    "failed": sorted(coordinator.data.failed)
                    if coordinator.data is not None
                    else None,
                }
                for name, coordinator in (
                    ("readings", runtime_data.readings),
                    ("settings", runtime_data.settings),
                )
            },
            "held_setpoints": {
                str(setpoint): {
                    "value": hold.value,
                    "failing": hold.failing,
                    "releasing": hold.releasing,
                }
                for setpoint in Setpoint
                if (hold := runtime_data.holds.get(setpoint)) is not None
            },
            "read_error": read_error,
            "raw": raw,
        },
        TO_REDACT,
    )
