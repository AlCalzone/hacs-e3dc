"""Tests for the setpoints the E3/DC integration holds."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from modbus_connection import (
    IllegalDataAddressError,
    IllegalFunctionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusUnit, WriteEvent
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.e3dc.const import SETTINGS_SCAN_INTERVAL
from custom_components.e3dc.e3dc_ess_modbus import Setpoint
from custom_components.e3dc.e3dc_ess_modbus.model import register
from custom_components.e3dc.holds import REWRITE_INTERVAL

BATTERY_POWER = "number.s10_x_battery_power_setpoint"
BATTERY_CONTROL = "switch.s10_x_battery_power_control"
FEED_IN_LIMIT = "number.s10_x_feed_in_limit_setpoint"
FEED_IN_CONTROL = "switch.s10_x_feed_in_limit_control"
ACTIVE_POWER_LIMIT = "number.s10_x_active_power_limit_setpoint"
ACTIVE_POWER_CONTROL = "switch.s10_x_active_power_limit_control"


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up the platforms with setpoint entities."""
    return [Platform.NUMBER, Platform.SWITCH]


@pytest.fixture
def writes(mock_unit: MockModbusUnit) -> list[WriteEvent]:
    """Every write the power station receives."""
    events: list[WriteEvent] = []
    mock_unit.on_write(events.append)
    return events


def _writes_to(writes: list[WriteEvent], index: int) -> list[list[int]]:
    return [write.values for write in writes if write.address == register(index)]


async def _set(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )


async def _switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(REWRITE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _refuse_fc16(monkeypatch: pytest.MonkeyPatch, unit: MockModbusUnit) -> None:
    monkeypatch.setattr(
        unit, "write_registers", AsyncMock(side_effect=IllegalFunctionError())
    )


@pytest.mark.usefixtures("init_integration")
async def test_battery_power_is_held_until_switched_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    writes: list[WriteEvent],
) -> None:
    """Test setting the battery power holds it until the control switch is off."""
    assert hass.states.get(BATTERY_POWER).state == STATE_UNKNOWN
    assert hass.states.get(BATTERY_CONTROL).state == STATE_OFF

    await _set(hass, BATTERY_POWER, 3000)

    assert hass.states.get(BATTERY_POWER).state == "3000.0"
    assert hass.states.get(BATTERY_CONTROL).state == STATE_ON

    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert _writes_to(writes, 40197) == [[3000, 0]] * 3

    await _switch(hass, BATTERY_CONTROL, False)
    await _tick(hass, freezer)

    assert _writes_to(writes, 40197) == [[3000, 0]] * 3
    assert _writes_to(writes, 40199) == [[1]]
    assert hass.states.get(BATTERY_CONTROL).state == STATE_OFF
    assert hass.states.get(BATTERY_POWER).state == "3000.0"

    await _switch(hass, BATTERY_CONTROL, True)

    assert _writes_to(writes, 40197)[-1] == [3000, 0]
    assert hass.states.get(BATTERY_CONTROL).state == STATE_ON


@pytest.mark.usefixtures("init_integration")
async def test_switching_on_without_a_value_raises(hass: HomeAssistant) -> None:
    """Test a control switch needs a value to hold."""
    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, FEED_IN_CONTROL, True)

    assert err.value.translation_key == "no_setpoint"


@pytest.mark.usefixtures("init_integration")
async def test_grid_limits_are_handed_back(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    writes: list[WriteEvent],
) -> None:
    """Test switching off lifts the active power limit and drops the feed-in limit."""
    await _set(hass, FEED_IN_LIMIT, 4000)
    await _set(hass, ACTIVE_POWER_LIMIT, 70)

    await _switch(hass, FEED_IN_CONTROL, False)
    await _switch(hass, ACTIVE_POWER_CONTROL, False)
    await _tick(hass, freezer)

    assert _writes_to(writes, 40186) == [[4000, 0]]
    assert _writes_to(writes, 40192) == [[700], [1000]]
    assert hass.states.get(FEED_IN_CONTROL).state == STATE_OFF
    assert hass.states.get(ACTIVE_POWER_CONTROL).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_a_failing_rewrite_makes_the_state_unknown(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    """Test a failing rewrite is logged once, shows unknown, and recovers."""
    await _set(hass, BATTERY_POWER, 3000)
    mock_unit.fail_write(register(40197), ModbusTimeoutError())

    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert caplog.text.count("Could not rewrite battery_power") == 1
    assert hass.states.get(BATTERY_CONTROL).state == STATE_UNKNOWN

    mock_unit.fail_write(register(40197), None)
    await _tick(hass, freezer)

    assert "Rewriting battery_power works again" in caplog.text
    assert hass.states.get(BATTERY_CONTROL).state == STATE_ON
    assert _writes_to(writes, 40197)[-1] == [3000, 0]


@pytest.mark.usefixtures("init_integration")
async def test_a_failed_release_is_retried(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    """Test a hand-back that fails shows unknown and is retried on every tick."""
    await _set(hass, BATTERY_POWER, 3000)
    mock_unit.fail_write(register(40199), ModbusTimeoutError())

    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, BATTERY_CONTROL, False)
    assert err.value.translation_key == "write_failed"
    assert hass.states.get(BATTERY_CONTROL).state == STATE_UNKNOWN

    await _tick(hass, freezer)
    assert hass.states.get(BATTERY_CONTROL).state == STATE_UNKNOWN

    mock_unit.fail_write(register(40199), None)
    await _tick(hass, freezer)

    assert "Released battery_power after an earlier failure" in caplog.text
    assert hass.states.get(BATTERY_CONTROL).state == STATE_OFF
    assert _writes_to(writes, 40197) == [[3000, 0]]


@pytest.mark.parametrize(
    ("entity_id", "value", "index", "translation_key"),
    [
        pytest.param(
            BATTERY_POWER, 3000, 40198, "battery_power_inconsistent", id="battery"
        ),
        pytest.param(
            FEED_IN_LIMIT, 70000, 40187, "feed_in_limit_inconsistent", id="feed_in"
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_a_half_written_setpoint_says_what_was_attempted(
    hass: HomeAssistant,
    mock_unit: MockModbusUnit,
    monkeypatch: pytest.MonkeyPatch,
    entity_id: str,
    value: int,
    index: int,
    translation_key: str,
) -> None:
    """Test a half-written pair raises an error naming what was put back."""
    _refuse_fc16(monkeypatch, mock_unit)
    mock_unit.fail_write(register(index), ModbusTimeoutError())

    with pytest.raises(HomeAssistantError) as err:
        await _set(hass, entity_id, value)

    assert err.value.translation_key == translation_key


@pytest.mark.usefixtures("init_integration")
async def test_a_half_written_rewrite_ends_the_hold(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    mock_unit: MockModbusUnit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a half-written rewrite ends the hold, since the library ended it too."""
    await _set(hass, BATTERY_POWER, 3000)
    _refuse_fc16(monkeypatch, mock_unit)
    mock_unit.fail_write(register(40198), ModbusTimeoutError())

    await _tick(hass, freezer)

    assert "Stopped holding battery_power after a half-written rewrite" in caplog.text
    assert hass.states.get(BATTERY_CONTROL).state == STATE_OFF


@pytest.mark.usefixtures("init_integration")
async def test_a_failed_first_write_raises(
    hass: HomeAssistant, mock_unit: MockModbusUnit
) -> None:
    """Test a setpoint the power station refuses raises a translated error."""
    mock_unit.fail_write(register(40192), ModbusTimeoutError())

    with pytest.raises(HomeAssistantError) as err:
        await _set(hass, ACTIVE_POWER_LIMIT, 50)

    assert err.value.translation_key == "write_failed"
    assert hass.states.get(ACTIVE_POWER_CONTROL).state == STATE_OFF


async def test_unloading_hands_the_battery_back(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    writes: list[WriteEvent],
) -> None:
    """Test unloading the entry ends remote battery control."""
    await _set(hass, BATTERY_POWER, 3000)

    await hass.config_entries.async_unload(init_integration.entry_id)

    assert _writes_to(writes, 40199) == [[1]]


async def test_a_failing_release_on_unload_is_logged(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a release that fails while unloading doesn't stop the unload."""
    await _set(hass, BATTERY_POWER, 3000)
    mock_unit.fail_write(register(40199), ModbusTimeoutError())

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert "Could not release battery_power" in caplog.text


@pytest.mark.usefixtures("init_integration")
async def test_stopping_home_assistant_hands_setpoints_back(
    hass: HomeAssistant, writes: list[WriteEvent]
) -> None:
    """Test the holds are released when Home Assistant stops."""
    await _set(hass, ACTIVE_POWER_LIMIT, 60)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert _writes_to(writes, 40192) == [[600], [1000]]


async def test_a_setpoint_during_unload_is_refused(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
    monkeypatch: pytest.MonkeyPatch,
    writes: list[WriteEvent],
) -> None:
    """Test a value set while the entry hands its setpoints back is refused."""
    await _set(hass, BATTERY_POWER, 3000)
    write_register = mock_unit.write_register
    releasing, release = asyncio.Event(), asyncio.Event()

    async def slow_release(address: int, value: int) -> None:
        if address == register(40199):
            releasing.set()
            await release.wait()
        await write_register(address, value)

    monkeypatch.setattr(mock_unit, "write_register", slow_release)
    unload = hass.async_create_task(
        hass.config_entries.async_unload(init_integration.entry_id)
    )
    await releasing.wait()
    holds = init_integration.runtime_data.holds
    setting = hass.async_create_task(holds.async_start(Setpoint.BATTERY_POWER, 2000))
    await asyncio.sleep(0)
    release.set()
    await unload

    with pytest.raises(HomeAssistantError) as err:
        await setting
    assert err.value.translation_key == "not_loaded"
    assert _writes_to(writes, 40197) == [[3000, 0]]


async def test_stopping_with_a_rewrite_in_flight(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
    monkeypatch: pytest.MonkeyPatch,
    writes: list[WriteEvent],
) -> None:
    """Test Home Assistant stopping mid-rewrite still hands the battery back."""
    await _set(hass, BATTERY_POWER, 3000)
    write_registers = mock_unit.write_registers
    started = asyncio.Event()

    async def hang(address: int, values: list[int]) -> None:
        started.set()
        await asyncio.sleep(60)
        await write_registers(address, values)

    monkeypatch.setattr(mock_unit, "write_registers", hang)
    freezer.tick(REWRITE_INTERVAL)
    async_fire_time_changed(hass)
    await started.wait()

    await hass.async_stop()

    assert _writes_to(writes, 40199) == [[1]]


async def test_holds_ignore_failed_settings_polls(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    """Test the setpoint entities stay usable while the settings poll fails."""
    await _set(hass, BATTERY_POWER, 3000)
    mock_unit.fail_read(register(40199), IllegalDataAddressError())
    freezer.tick(timedelta(seconds=SETTINGS_SCAN_INTERVAL))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "battery_control" in init_integration.runtime_data.settings.data.failed

    assert hass.states.get(BATTERY_CONTROL).state == STATE_ON
    await _switch(hass, BATTERY_CONTROL, False)

    assert _writes_to(writes, 40199) == [[1]]


@pytest.mark.usefixtures("init_integration")
async def test_a_failed_setpoint_keeps_the_old_value(
    hass: HomeAssistant, mock_unit: MockModbusUnit
) -> None:
    """Test a value the power station refuses doesn't replace the held one."""
    await _set(hass, BATTERY_POWER, 1000)
    mock_unit.fail_write(register(40197), ModbusTimeoutError())

    with pytest.raises(HomeAssistantError):
        await _set(hass, BATTERY_POWER, 2000)

    assert hass.states.get(BATTERY_POWER).state == "1000.0"
    assert hass.states.get(BATTERY_CONTROL).state == STATE_ON


async def test_the_number_is_restored_without_holding(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_get_unit: MagicMock,
    writes: list[WriteEvent],
) -> None:
    """Test a restored value can be held again but isn't written on its own."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(BATTERY_POWER, "2500.0"),
                {
                    "native_max_value": 50000,
                    "native_min_value": -50000,
                    "native_step": 100,
                    "native_unit_of_measurement": "W",
                    "native_value": 2500,
                },
            )
        ],
    )
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.e3dc.PLATFORMS", [Platform.NUMBER, Platform.SWITCH]):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get(BATTERY_POWER).state == "2500"
    assert hass.states.get(BATTERY_CONTROL).state == STATE_OFF
    assert _writes_to(writes, 40197) == []

    await _switch(hass, BATTERY_CONTROL, True)

    assert _writes_to(writes, 40197) == [[2500, 0]]


async def test_a_failed_setup_stops_the_rewrite_timer(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_get_unit: MagicMock,
) -> None:
    """Test the rewrite timer doesn't outlive a setup that failed late."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("platform bug"),
        ),
        patch(
            "custom_components.e3dc.holds.E3dcHolds._async_on_interval"
        ) as on_interval,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await _tick(hass, freezer)

    on_interval.assert_not_called()
