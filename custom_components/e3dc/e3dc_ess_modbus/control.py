"""Remote control of the grid connection and the battery, registers 40186 to 40210.

Several of these registers are write-only, so the components read only the
readable ones and offer a method per write-only setpoint. Each setpoint holds
for a timeout, 30 seconds by default, so a caller rewrites it to keep it.
"""

from __future__ import annotations

from modbus_connection.encode import encode_int
from modbus_connection.model import boolean, enum, int32, integer

from .enums import InverterMode, InverterModeRequest
from .model import E3dcComponent, register


def _timeout(value: int) -> int:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"timeout {value} s is outside 0-65535")
    return value


def _tenths(percent: float, low: float, high: float) -> int:
    """A percentage as the device's 0.1 % steps, checked against its range."""
    if not low <= percent <= high:
        raise ValueError(f"{percent} % is outside {low}-{high}")
    return round(percent * 10)


class GridControl(E3dcComponent):
    """Feed-in limitation and active and reactive power control.

    The device takes these only when it is certified for grid operator control.
    """

    register_ranges = (
        (register(40186), register(40188)),
        (register(40194), register(40194)),
        (register(40196), register(40196)),
    )

    feed_in_limit = int32(register(40186), word_order="little", unit="W", writable=True)
    """The last limit written, not the limit in effect."""
    power_output_ready = boolean(register(40188))
    """PV inverter on the grid, DC-DC converter working, battery dischargeable."""
    active_power_failsafe_timeout = integer(
        register(40194), signed=False, unit="s", writable=_timeout
    )
    """How long an active power setpoint holds; 0 turns the failsafe off."""
    reactive_power_failsafe_timeout = integer(
        register(40196), signed=False, unit="s", writable=_timeout
    )
    """How long a reactive power setpoint holds; 0 turns the failsafe off."""

    async def async_write_feed_in_limit(self, watts: int) -> None:
        """Cap the power fed into the grid; rewrite within 30 seconds to keep it."""
        await self.write("feed_in_limit", watts)

    async def async_write_reactive_power(self, var: int) -> None:
        """Set the reactive power, in var, within ±50 % of the rated power."""
        await self._unit.write_register(register(40189), _word(var))

    async def async_write_cos_phi(self, value: int) -> None:
        """Set the reactive power as cos φ, from -600 (0.4 capacitive) to 600."""
        if not -600 <= value <= 600:
            raise ValueError(f"cos φ setpoint {value} is outside -600-600")
        await self._unit.write_register(register(40190), _word(value))

    async def async_write_reactive_power_percent(self, percent: float) -> None:
        """Set the reactive power as a percentage of the rated apparent power."""
        tenths = _tenths(percent, -100, 100)
        await self._unit.write_register(register(40191), _word(tenths))

    async def async_write_active_power_limit(self, percent: float) -> None:
        """Cap the inverter's active power, ignoring the house's consumption."""
        tenths = _tenths(percent, 0, 100)
        await self._unit.write_register(register(40192), _word(tenths))

    async def async_write_active_power_failsafe(self, percent: float) -> None:
        """Set the active power limit that applies once a setpoint times out."""
        tenths = _tenths(percent, 0, 100)
        await self._unit.write_register(register(40193), _word(tenths))

    async def async_write_reactive_power_failsafe(self, percent: float) -> None:
        """Set the reactive power that applies once a setpoint times out."""
        tenths = _tenths(percent, 0, 100)
        await self._unit.write_register(register(40195), _word(tenths))


class BatteryControl(E3dcComponent):
    """Remote control of the battery and the inverter operating mode."""

    register_ranges = ((register(40199), register(40201)),)

    remote_control_disabled = boolean(register(40199))
    inverter_mode_request = enum(
        register(40200), InverterModeRequest, signed=True, writable=True
    )
    inverter_mode = enum(register(40201), InverterMode, signed=True)

    async def async_write_battery_power(self, watts: int) -> None:
        """Charge (positive) or discharge (negative) the battery at ``watts``.

        PV power charges first and the grid makes up the rest. The device
        returns to normal control unless this is rewritten within 30 seconds.
        """
        words = encode_int(watts, count=2, word_order="little")
        await self._unit.write_registers(register(40197), words)

    async def async_write_battery_power_percent(self, percent: float) -> None:
        """Charge (positive) or discharge (negative) at a share of full power."""
        tenths = _tenths(percent, -100, 100)
        await self._unit.write_register(register(40210), _word(tenths))

    async def async_stop_remote_control(self) -> None:
        """End remote battery control at once, without waiting for its timeout."""
        await self._unit.write_register(register(40199), 1)

    async def async_write_inverter_mode(self, mode: InverterModeRequest) -> None:
        """Force an inverter operating mode, or hand control back."""
        await self.write("inverter_mode_request", mode)


class BatteryLimits(E3dcComponent):
    """What the battery can take and give right now, and what it holds."""

    max_charge_power = int32(register(40202), word_order="little", unit="W")
    max_discharge_power = int32(register(40204), word_order="little", unit="W")
    """Negative."""
    remaining_energy = int32(register(40206), word_order="little", unit="Wh")
    full_charge_energy = int32(register(40208), word_order="little", unit="Wh")


def _word(value: int) -> int:
    """A signed 16-bit value as the register word that carries it."""
    return encode_int(value, count=1)[0]
