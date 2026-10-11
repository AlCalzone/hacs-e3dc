"""Services for the E3/DC integration."""

from collections.abc import Awaitable

import probatio
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.service import (
    async_get_config_entry,
    async_register_admin_service,
)
from modbus_connection import ModbusError

from .const import DOMAIN
from .coordinator import E3dcConfigEntry

SERVICE_SET_ACTIVE_POWER_LIMIT = "set_active_power_limit"
SERVICE_SET_BATTERY_POWER = "set_battery_power"
SERVICE_SET_FEED_IN_LIMIT = "set_feed_in_limit"
SERVICE_STOP_BATTERY_CONTROL = "stop_battery_control"

ATTR_LIMIT = "limit"
ATTR_POWER = "power"

_ENTRY_SCHEMA = probatio.Schema({probatio.Required(ATTR_CONFIG_ENTRY_ID): str})

# The selectors in services.yaml bound only the UI. This bounds scripted calls.
_POWER_RANGE = probatio.All(
    probatio.Coerce(int), probatio.Range(min=-100000, max=100000)
)

SET_BATTERY_POWER_SCHEMA = _ENTRY_SCHEMA.extend(
    {probatio.Required(ATTR_POWER): _POWER_RANGE}
)

SET_FEED_IN_LIMIT_SCHEMA = _ENTRY_SCHEMA.extend(
    {
        probatio.Required(ATTR_POWER): probatio.All(
            probatio.Coerce(int), probatio.Range(min=0, max=100000)
        )
    }
)

SET_ACTIVE_POWER_LIMIT_SCHEMA = _ENTRY_SCHEMA.extend(
    {
        probatio.Required(ATTR_LIMIT): probatio.All(
            probatio.Coerce(float), probatio.Percentage()
        )
    }
)


def _get_entry(
    hass: HomeAssistant, call: ServiceCall, component: str
) -> E3dcConfigEntry:
    """Return a loaded entry whose power station serves the needed registers."""
    entry: E3dcConfigEntry = async_get_config_entry(
        hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID]
    )
    if component not in entry.runtime_data.served_components:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unsupported_action",
            translation_placeholders={"title": entry.title},
        )
    return entry


async def _write(entry: E3dcConfigEntry, write: Awaitable[None]) -> None:
    """Translate a failed write, then let the settings sensors catch up."""
    try:
        await write
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_action_value",
        ) from err
    except ModbusError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="write_failed",
        ) from err
    await entry.runtime_data.settings.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the E3/DC services."""

    async def _handle_set_battery_power(call: ServiceCall) -> None:
        entry = _get_entry(hass, call, "battery_control")
        battery = entry.runtime_data.device.battery_control
        assert battery is not None
        await _write(entry, battery.async_write_battery_power(call.data[ATTR_POWER]))

    async def _handle_stop_battery_control(call: ServiceCall) -> None:
        entry = _get_entry(hass, call, "battery_control")
        battery = entry.runtime_data.device.battery_control
        assert battery is not None
        await _write(entry, battery.async_stop_remote_control())

    async def _handle_set_feed_in_limit(call: ServiceCall) -> None:
        entry = _get_entry(hass, call, "grid_control")
        grid = entry.runtime_data.device.grid_control
        assert grid is not None
        await _write(entry, grid.async_write_feed_in_limit(call.data[ATTR_POWER]))

    async def _handle_set_active_power_limit(call: ServiceCall) -> None:
        entry = _get_entry(hass, call, "grid_control")
        grid = entry.runtime_data.device.grid_control
        assert grid is not None
        await _write(entry, grid.async_write_active_power_limit(call.data[ATTR_LIMIT]))

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_BATTERY_POWER,
        _handle_set_battery_power,
        schema=SET_BATTERY_POWER_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_STOP_BATTERY_CONTROL,
        _handle_stop_battery_control,
        schema=_ENTRY_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_FEED_IN_LIMIT,
        _handle_set_feed_in_limit,
        schema=SET_FEED_IN_LIMIT_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_ACTIVE_POWER_LIMIT,
        _handle_set_active_power_limit,
        schema=SET_ACTIVE_POWER_LIMIT_SCHEMA,
    )
