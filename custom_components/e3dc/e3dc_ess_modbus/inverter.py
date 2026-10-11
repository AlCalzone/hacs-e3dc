"""Inverter data, 34 registers per inverter from register 41000."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from modbus_connection.model import ComponentGroup, gauge, int32, integer

from .model import E3dcComponent, register

INVERTER_COUNT = 8
INVERTER_STRIDE = 34

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit


class Inverter(E3dcComponent):
    """One inverter, placed with ``base_offset``.

    Inverter 0 is the device's own. Inverters 1 to 7 are additional solar
    inverters. The int32 values arrive low word first.
    """

    apparent_power_l1 = int32(register(41000), word_order="little", unit="VA")
    apparent_power_l2 = int32(register(41002), word_order="little", unit="VA")
    apparent_power_l3 = int32(register(41004), word_order="little", unit="VA")
    active_power_l1 = int32(register(41006), word_order="little", unit="W")
    active_power_l2 = int32(register(41008), word_order="little", unit="W")
    active_power_l3 = int32(register(41010), word_order="little", unit="W")
    reactive_power_l1 = int32(register(41012), word_order="little", unit="var")
    reactive_power_l2 = int32(register(41014), word_order="little", unit="var")
    reactive_power_l3 = int32(register(41016), word_order="little", unit="var")
    voltage_l1 = gauge(register(41018), 0.1, unit="V")
    voltage_l2 = gauge(register(41019), 0.1, unit="V")
    voltage_l3 = gauge(register(41020), 0.1, unit="V")
    current_l1 = gauge(register(41021), 0.01, unit="A")
    current_l2 = gauge(register(41022), 0.01, unit="A")
    current_l3 = gauge(register(41023), 0.01, unit="A")
    frequency = gauge(register(41024), 0.01, unit="Hz")
    dc_power_1 = integer(register(41025), unit="W")
    dc_power_2 = integer(register(41026), unit="W")
    dc_power_3 = integer(register(41027), unit="W")
    """The manual marks tracker 3 as not used."""
    dc_voltage_1 = gauge(register(41028), 0.1, unit="V")
    dc_voltage_2 = gauge(register(41029), 0.1, unit="V")
    dc_voltage_3 = gauge(register(41030), 0.1, unit="V")
    dc_current_1 = gauge(register(41031), 0.01, unit="A")
    dc_current_2 = gauge(register(41032), 0.01, unit="A")
    dc_current_3 = gauge(register(41033), 0.01, unit="A")

    @property
    def is_connected(self) -> bool:
        """Whether any phase reports a grid voltage."""
        return any(
            voltage for voltage in (self.voltage_l1, self.voltage_l2, self.voltage_l3)
        )


class Inverters(ComponentGroup, Mapping[int, Inverter]):
    """The inverter slots that answer their registers, keyed by slot."""

    # Compare and hash by identity, the way a component does
    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init__(self, unit: ModbusUnit, slots: dict[int, Inverter]) -> None:
        """Set up the group over the slots setup found."""
        self._slots = slots
        super().__init__(unit, slots.values())

    def __getitem__(self, slot: int) -> Inverter:
        """The inverter in ``slot``."""
        return self._slots[slot]

    def __iter__(self) -> Iterator[int]:
        """The slots, in order."""
        return iter(self._slots)

    def __len__(self) -> int:
        """How many slots answered at setup."""
        return len(self._slots)
