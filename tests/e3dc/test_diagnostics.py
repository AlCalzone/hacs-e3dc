"""Tests for the E3/DC diagnostics."""

from homeassistant.core import HomeAssistant
from modbus_connection import ModbusConnectionError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.e3dc.e3dc_ess_modbus import register


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test generating diagnostics for a config entry."""
    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert diag == snapshot


async def test_diagnostics_drop_the_serial_number_registers(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Test the raw dump leaves out the serial number's registers."""
    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    holding = diag["raw"]["holding"]
    assert str(register(40035)) in holding
    assert not any(str(register(n)) in holding for n in range(40036, 40052))
    assert str(register(40052)) in holding
    assert diag["serial_number"] == "**REDACTED**"


async def test_diagnostics_when_unreachable(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a failed read still produces a diagnostics download."""
    mock_unit.fail_requests(ModbusConnectionError())

    diag = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert diag["read_error"] == "ModbusConnectionError"
    assert diag["raw"] is None
