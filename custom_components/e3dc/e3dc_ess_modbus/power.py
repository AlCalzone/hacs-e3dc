"""Power flows and DC strings, registers 40068 to 40104."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from modbus_connection.model import (
    ComponentGroup,
    bits,
    enum,
    flags,
    gauge,
    int32,
    integer,
)

from .enums import EmergencyPowerStatus, EmsStatus
from .model import E3dcComponent, register

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

DC_STRING_COUNT = 3


class PowerFlows(E3dcComponent):
    """Where the power goes: solar, battery, house, grid and wallboxes.

    The int32 values arrive low word first.
    """

    pv_power = int32(register(40068), word_order="little", unit="W")
    battery_power = int32(register(40070), word_order="little", unit="W")
    """Positive while charging, negative while discharging."""
    house_power = int32(register(40072), word_order="little", unit="W")
    grid_power = int32(register(40074), word_order="little", unit="W")
    """Positive while importing, negative while feeding in."""
    additional_power = int32(register(40076), word_order="little", unit="W")
    """The sum of every additional feeder, such as a second PV inverter."""
    wallbox_power = int32(register(40078), word_order="little", unit="W")
    wallbox_solar_power = int32(register(40080), word_order="little", unit="W")
    """The part of the wallbox power that comes from the sun."""
    autarky = bits(register(40082), 8, 8, unit="%")
    """Self-sufficiency, the high byte of register 40082."""
    self_consumption = bits(register(40082), 0, 8, unit="%")
    """The low byte of register 40082."""
    battery_soc = integer(register(40083), signed=False, unit="%")
    emergency_power_status = enum(register(40084), EmergencyPowerStatus)
    ems_status = flags(register(40085), EmsStatus)


class DcString(E3dcComponent):
    """One DC string of the built-in solar inverter, placed by ``index``.

    The manual marks string 3 as not used.
    """

    voltage = integer(register(40096), signed=False, stride=1, unit="V")
    current = gauge(register(40099), 0.01, signed=False, stride=1, unit="A")
    power = integer(register(40102), signed=False, stride=1, unit="W")


class DcStrings(ComponentGroup, Mapping[int, DcString]):
    """The DC strings 1 to 3, read as one because their registers interleave."""

    # Compare and hash by identity, the way a component does
    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init__(self, unit: ModbusUnit) -> None:
        """Set up the strings."""
        self._strings = {
            number: DcString(unit, index=number)
            for number in range(1, DC_STRING_COUNT + 1)
        }
        super().__init__(unit, self._strings.values())

    def __getitem__(self, number: int) -> DcString:
        """DC string ``number``, from 1 to 3."""
        return self._strings[number]

    def __iter__(self) -> Iterator[int]:
        """The string numbers, 1 first."""
        return iter(self._strings)

    def __len__(self) -> int:
        """How many DC strings there are."""
        return len(self._strings)
