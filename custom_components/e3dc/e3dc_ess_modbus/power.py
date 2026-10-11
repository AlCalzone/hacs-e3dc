"""Power flows and DC strings, registers 40068 to 40104."""

from __future__ import annotations

from modbus_connection.model import bits, enum, flags, gauge, int32, integer

from .enums import EmergencyPowerStatus, EmsStatus
from .model import E3dcComponent, register


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
