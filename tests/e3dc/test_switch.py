"""Tests for the E3/DC switch platform."""

import pytest
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from modbus_connection import IllegalDataValueError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.e3dc.e3dc_ess_modbus.model import register


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the switch platform."""
    return [Platform.SWITCH]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_all_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Test all entities match their snapshot."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    ("entity_id", "service", "word"),
    [
        pytest.param(
            "switch.wallbox_0_charging",
            SERVICE_TURN_OFF,
            0b0000000111111,
            id="abort_charging",
        ),
        pytest.param(
            "switch.wallbox_0_solar_charging",
            SERVICE_TURN_OFF,
            0b0000000111001,
            id="hybrid_mode",
        ),
        pytest.param(
            "switch.wallbox_0_single_phase_charging",
            SERVICE_TURN_ON,
            0b1000000111011,
            id="one_phase",
        ),
    ],
)
async def test_switch_writes_its_bit(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
    entity_id: str,
    service: str,
    word: int,
) -> None:
    """Test a wallbox switch changes only its own bit of the control word."""
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert mock_unit.holding[register(40088)] == word


async def test_switch_write_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_unit: MockModbusUnit,
) -> None:
    """Test a write the power station rejects raises a translated error."""
    mock_unit.fail_write(register(40088), IllegalDataValueError())

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "switch.wallbox_0_charging"},
            blocking=True,
        )
    assert err.value.translation_key == "write_failed"
