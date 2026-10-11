"""Tests for the E3/DC config flow."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, _patch, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from modbus_connection import ModbusTcpParams, ModbusTimeoutError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.e3dc.const import DOMAIN

from . import MOCK_ENTRY_DATA, MOCK_MODEL, MOCK_SERIAL, seed, seed_identity


def _patch_temporary_unit(connection: MockModbusConnection) -> _patch[AsyncMock]:
    """Stand in for async_get_temporary_unit, handing out a unit on connection."""

    @asynccontextmanager
    async def _get_temporary_unit(
        hass: HomeAssistant, params: ModbusTcpParams, unit_id: int
    ) -> AsyncIterator[MockModbusUnit]:
        yield connection.for_unit(unit_id)

    return patch(
        "custom_components.e3dc.config_flow.async_get_temporary_unit",
        side_effect=_get_temporary_unit,
    )


class _RaisingTemporaryUnit:
    """Stand in for a temporary-unit context whose __aenter__ raises."""

    def __init__(self, error: Exception) -> None:
        """Initialize with the error to raise on entry."""
        self._error = error

    async def __aenter__(self) -> None:
        raise self._error

    async def __aexit__(self, *args: object) -> None:
        """No cleanup: entry never succeeded."""


@pytest.fixture
def identified() -> MockModbusConnection:
    """A connection to a power station that answers its identity."""
    connection = MockModbusConnection()
    seed_identity(connection.for_unit(1))
    return connection


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_step_success(
    hass: HomeAssistant, identified: MockModbusConnection
) -> None:
    """Test the flow probes the power station and creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _patch_temporary_unit(identified) as mock_temporary_unit:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.100", CONF_PORT: 1502}
        )

    mock_temporary_unit.assert_called_once_with(
        hass, ModbusTcpParams(host="192.168.1.100", port=1502), 1
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_MODEL
    assert result["data"] == {CONF_HOST: "192.168.1.100", CONF_PORT: 1502}
    assert result["result"].unique_id == MOCK_SERIAL


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(ModbusTimeoutError(), "cannot_connect", id="timeout"),
        pytest.param(HomeAssistantError("in use"), "cannot_connect", id="in_use"),
        pytest.param(RuntimeError("bug"), "unknown", id="unexpected"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_step_cannot_connect(
    hass: HomeAssistant,
    identified: MockModbusConnection,
    error: Exception,
    expected: str,
) -> None:
    """Test a failed probe shows an error and the flow recovers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.e3dc.config_flow.async_get_temporary_unit",
        return_value=_RaisingTemporaryUnit(error),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_ENTRY_DATA
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    with _patch_temporary_unit(identified):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_ENTRY_DATA
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("holding", "expected"),
    [
        pytest.param({40001: 0x5375}, "not_e3dc", id="sunspec_mode"),
        pytest.param({40001: 0xE3DC}, "no_serial_number", id="no_serial"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_step_wrong_device(
    hass: HomeAssistant, holding: dict[int, int | list[int]], expected: str
) -> None:
    """Test a device that is not a usable E3/DC power station is refused."""
    connection = MockModbusConnection()
    seed(connection.for_unit(1), holding)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_temporary_unit(connection):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_ENTRY_DATA
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    seed_identity(connection.for_unit(1))
    with _patch_temporary_unit(connection):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_ENTRY_DATA
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_step_already_configured(
    hass: HomeAssistant,
    identified: MockModbusConnection,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a power station that is already set up aborts the flow."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_temporary_unit(identified):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], MOCK_ENTRY_DATA
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure(
    hass: HomeAssistant,
    identified: MockModbusConnection,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test reconfiguring moves the entry to a new address."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with _patch_temporary_unit(identified):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.200", CONF_PORT: 502}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "192.168.1.200"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_other_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test reconfiguring onto another power station aborts."""
    connection = MockModbusConnection()
    seed_identity(connection.for_unit(1), serial="S10-99999999999")
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_temporary_unit(connection):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.200", CONF_PORT: 502}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert mock_config_entry.data == MOCK_ENTRY_DATA


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_cannot_connect(
    hass: HomeAssistant,
    identified: MockModbusConnection,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a failed probe during reconfigure shows an error and recovers."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.e3dc.config_flow.async_get_temporary_unit",
        return_value=_RaisingTemporaryUnit(ModbusTimeoutError()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.200", CONF_PORT: 502}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    with _patch_temporary_unit(identified):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.200", CONF_PORT: 502}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_reloads_a_loaded_entry(
    hass: HomeAssistant,
    identified: MockModbusConnection,
    init_integration: MockConfigEntry,
) -> None:
    """Test reconfiguring a loaded entry sets it up again on the new port."""
    result = await init_integration.start_reconfigure_flow(hass)
    with (
        _patch_temporary_unit(identified),
        patch(
            "custom_components.e3dc.async_setup_entry", return_value=True
        ) as mock_setup,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.100", CONF_PORT: 1502}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert init_integration.data[CONF_PORT] == 1502
    mock_setup.assert_called_once()
