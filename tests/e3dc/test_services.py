"""Tests for the E3/DC services."""

from typing import Any
from unittest.mock import MagicMock

import probatio
import pytest
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from modbus_connection import IllegalDataAddressError, IllegalDataValueError
from modbus_connection.mock import MockModbusUnit, WriteEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.e3dc.const import DOMAIN
from custom_components.e3dc.e3dc_ess_modbus import register
from custom_components.e3dc.services import (
    SERVICE_SET_ACTIVE_POWER_LIMIT,
    SERVICE_SET_BATTERY_POWER,
    SERVICE_SET_FEED_IN_LIMIT,
    SERVICE_STOP_BATTERY_CONTROL,
)


@pytest.fixture
def writes(mock_unit: MockModbusUnit) -> list[WriteEvent]:
    """Every write the power station receives."""
    events: list[WriteEvent] = []
    mock_unit.on_write(events.append)
    return events


@pytest.mark.parametrize(
    ("service", "data", "address", "values"),
    [
        pytest.param(
            SERVICE_SET_BATTERY_POWER,
            {"power": -3000},
            register(40197),
            [0xF448, 0xFFFF],
            id="battery_power",
        ),
        pytest.param(SERVICE_STOP_BATTERY_CONTROL, {}, register(40199), [1], id="stop"),
        pytest.param(
            SERVICE_SET_FEED_IN_LIMIT,
            {"power": 4000},
            register(40186),
            [4000, 0],
            id="feed_in_limit",
        ),
        pytest.param(
            SERVICE_SET_ACTIVE_POWER_LIMIT,
            {"limit": 70},
            register(40192),
            [700],
            id="active_power_limit",
        ),
    ],
)
async def test_service_writes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    writes: list[WriteEvent],
    service: str,
    data: dict[str, Any],
    address: int,
    values: list[int],
) -> None:
    """Test each service writes its setpoint to the power station."""
    await hass.services.async_call(
        DOMAIN,
        service,
        {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, **data},
        blocking=True,
    )

    assert [(write.address, write.values) for write in writes] == [(address, values)]


async def test_service_rejects_old_firmware(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_unit: MagicMock,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a service the firmware does not serve raises a validation error."""
    mock_unit.fail_read(register(40199), IllegalDataAddressError())
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BATTERY_POWER,
            {ATTR_CONFIG_ENTRY_ID: mock_config_entry.entry_id, "power": 1000},
            blocking=True,
        )
    assert err.value.translation_key == "unsupported_action"


async def test_service_write_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a write the power station rejects raises a translated error."""
    mock_unit.fail_write(register(40197), IllegalDataValueError())

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BATTERY_POWER,
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "power": 1000},
            blocking=True,
        )
    assert err.value.translation_key == "write_failed"


async def test_service_rejects_out_of_range_power(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test the schema bounds a scripted call the UI selector cannot."""
    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BATTERY_POWER,
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "power": 200000},
            blocking=True,
        )
