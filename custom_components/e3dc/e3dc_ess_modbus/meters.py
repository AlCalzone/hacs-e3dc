"""Power meters, registers 40105 to 40185."""

from __future__ import annotations

from modbus_connection.model import enum, int32, integer, repeating_group

from .enums import PowerMeterType
from .model import E3dcComponent, register

POWER_METER_COUNT = 8


class PowerMeter(E3dcComponent):
    """One meter slot's type and per-phase power, in 16 bits."""

    meter_type = enum(register(40105), PowerMeterType)
    power_l1 = integer(register(40106), unit="W")
    power_l2 = integer(register(40107), unit="W")
    power_l3 = integer(register(40108), unit="W")


class PowerMeters(E3dcComponent):
    """The eight power meter slots."""

    meters = repeating_group(POWER_METER_COUNT, PowerMeter, stride=4)


class PowerMeterPhases(E3dcComponent):
    """One meter slot's per-phase power, in 32 bits."""

    power_l1 = int32(register(40138), word_order="little", unit="W")
    power_l2 = int32(register(40140), word_order="little", unit="W")
    power_l3 = int32(register(40142), word_order="little", unit="W")


class WidePowerMeters(E3dcComponent):
    """The SG Ready status and the 32-bit phase powers of all eight meters.

    Firmware without these registers refuses the block.
    """

    sg_ready_status = integer(register(40137), signed=False)
    """The manual documents no values for this register."""
    meters = repeating_group(POWER_METER_COUNT, PowerMeterPhases, stride=6)
