"""Tests for the E3/DC integration setup."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTcpParams,
)
from modbus_connection.mock import MockModbusConnection, MockModbusUnit
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.e3dc.const import DOMAIN, SCAN_INTERVAL, UNIT_ID
from custom_components.e3dc.e3dc_ess_modbus import register

from . import MOCK_SERIAL, seed

_SCAN = timedelta(seconds=SCAN_INTERVAL)


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_get_unit: MagicMock,
) -> None:
    """Test the entry sets up over a unit from modbus and unloads."""
    assert init_integration.state is ConfigEntryState.LOADED
    mock_get_unit.assert_called_once_with(
        hass,
        init_integration,
        ModbusTcpParams(host="192.168.1.100", port=502),
        UNIT_ID,
    )

    await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_device_info(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
    init_integration: MockConfigEntry,
) -> None:
    """Test the power station and its parts land in the device registry."""
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert (
        sorted(
            (device.name, device.manufacturer, device.model, device.sw_version)
            for device in devices
        )
        == snapshot
    )


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_unit: MagicMock,
    mock_unit: MockModbusUnit,
) -> None:
    """Test an unreachable power station retries setup."""
    mock_unit.fail_requests(ModbusConnectionError())
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_fails_when_not_e3dc(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_unit: MagicMock,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a device switched to SunSpec mode fails setup for good."""
    seed(mock_unit, {40001: 0x5375})
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert (
        mock_config_entry.reason
        == "The device is not an E3/DC power station in E3/DC protocol mode"
    )


@pytest.mark.parametrize("platforms", [[Platform.SENSOR]])
async def test_failed_component_goes_unavailable(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test only the entities of a refused block go unavailable."""
    mock_unit.fail_read(register(40105), IllegalDataAddressError())
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.power_meter_0_power").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.s10_x_pv_power").state == "5321"

    mock_unit.fail_read(register(40105), None)
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.power_meter_0_power").state == "50"


@pytest.mark.parametrize("platforms", [[Platform.SENSOR]])
async def test_dead_link_makes_everything_unavailable(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a dropped link marks the power station unavailable until it answers."""
    mock_unit.fail_requests(ModbusConnectionError())
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.s10_x_pv_power").state == STATE_UNAVAILABLE

    mock_unit.fail_requests(None)
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.s10_x_pv_power").state == "5321"


@pytest.mark.parametrize("platforms", [[Platform.SENSOR]])
async def test_every_component_failing_marks_the_poll_failed(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a poll where no component answers fails as a whole."""
    for index in (40068, 40096, 40088, 40105, 40137, 40202, 41000):
        mock_unit.fail_read(register(index), IllegalDataAddressError())
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    coordinator = init_integration.runtime_data.readings
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.s10_x_pv_power").state == STATE_UNAVAILABLE


@pytest.mark.parametrize("platforms", [[Platform.SWITCH]])
async def test_wallbox_added_when_it_appears(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a wallbox the power station starts reporting gets its entities."""
    assert hass.states.get("switch.wallbox_1_charging") is None

    seed(mock_unit, {40089: 0b1})
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("switch.wallbox_1_charging").state == "on"


async def test_remove_stale_wallbox(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a wallbox can be removed once the power station stops reporting it."""
    assert (
        await _remove(
            hass, hass_ws_client, device_registry, init_integration, "wallbox_0"
        )
        is False
    )

    seed(mock_unit, {40088: 0})
    freezer.tick(_SCAN)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert (
        await _remove(
            hass, hass_ws_client, device_registry, init_integration, "wallbox_0"
        )
        is True
    )
    assert (
        await _remove(hass, hass_ws_client, device_registry, init_integration, None)
        is False
    )


async def _remove(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
    part: str | None,
) -> bool:
    """Ask to remove the power station, or one of its parts, from the entry."""
    identifier = MOCK_SERIAL if part is None else f"{MOCK_SERIAL}_{part}"
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device is not None
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)
    response = await client.remove_device(device.id)
    return bool(response["success"])


async def test_remove_wallbox_of_unloaded_entry(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    init_integration: MockConfigEntry,
    mock_connection: MockModbusConnection,
) -> None:
    """Test a wallbox device can be removed while the entry is not loaded."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    assert (
        await _remove(
            hass, hass_ws_client, device_registry, init_integration, "wallbox_0"
        )
        is True
    )
