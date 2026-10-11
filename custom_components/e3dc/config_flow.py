"""Config flow for E3/DC devices."""

import logging
from collections.abc import Mapping
from typing import Any, override

import probatio
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)
from modbus_connection import ModbusError, ModbusTcpParams

from .const import DEFAULT_NAME, DEFAULT_PORT, DOMAIN, UNIT_ID
from .e3dc_ess_modbus import E3dcPowerStation, NotAnE3dcError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(),
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): probatio.All(
            NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, min=1, max=65535)
            ),
            probatio.Coerce(int),
        ),
    }
)


def create_modbus_params(data: Mapping[str, Any]) -> ModbusTcpParams:
    """Build the Modbus link parameters from config entry data."""
    return ModbusTcpParams(host=data[CONF_HOST], port=data[CONF_PORT])


async def _async_probe(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> E3dcPowerStation:
    """Connect to the power station and read its identity, or raise."""
    params = create_modbus_params(data)
    async with async_get_temporary_unit(hass, params, UNIT_ID) as unit:
        device = await E3dcPowerStation.async_detect(unit)
        device.close()
        return device


class E3dcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an E3/DC config flow."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask where the power station is, then probe it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device, errors = await self._async_validate(user_input)
            if device is not None:
                await self.async_set_unique_id(device.serial_number)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=device.model or DEFAULT_NAME, data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle updating an existing entry's connection details."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            device, errors = await self._async_validate(user_input)
            if device is not None:
                await self.async_set_unique_id(device.serial_number)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    async def _async_validate(
        self, data: dict[str, Any]
    ) -> tuple[E3dcPowerStation | None, dict[str, str]]:
        """Probe the power station, returning it or the errors to show instead."""
        try:
            device = await _async_probe(self.hass, data)
        except NotAnE3dcError:
            return None, {"base": "not_e3dc"}
        except (ModbusError, HomeAssistantError) as err:
            _LOGGER.debug("Could not reach the power station: %r", err)
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error while probing the power station")
            return None, {"base": "unknown"}

        if not device.serial_number:
            return None, {"base": "no_serial_number"}

        return device, {}
