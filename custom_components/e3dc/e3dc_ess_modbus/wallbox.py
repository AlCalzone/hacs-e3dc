"""Wallbox control words, registers 40088 to 40095."""

from __future__ import annotations

import asyncio
from typing import Any

from modbus_connection import ModbusError
from modbus_connection.model import bit, repeating_group

from .errors import InvalidValueError
from .model import E3dcComponent, register

WALLBOX_COUNT = 8


class Wallbox(E3dcComponent):
    """One wallbox's ``WallBox_X_CTRL`` word.

    Which bits a wallbox serves depends on its model. A write re-reads the
    word and changes only its own bit.
    """

    available = bit(register(40088), 0)
    """A wallbox is configured in this slot and answers."""
    solar_mode = bit(register(40088), 1, writable=True)
    """Charge from solar only; unset charges in hybrid mode."""
    charging_aborted = bit(register(40088), 2, writable=True)
    """Charging is not allowed. This is the bit the charging switch writes."""
    charging = bit(register(40088), 3)
    """A vehicle is drawing power. The wallbox sets this bit itself."""
    type2_locked = bit(register(40088), 4)
    type2_plugged = bit(register(40088), 5)
    """Multi connect and efy wallboxes set this only with a vehicle attached."""
    schuko_on = bit(register(40088), 6, writable=True)
    """The type F (CEE 7/3) socket is switched on.

    Only the Wallbox classic can switch it. Multi connect I reports it read-only,
    and the other models don't have it.
    """
    schuko_plugged = bit(register(40088), 7)
    schuko_locked = bit(register(40088), 8)
    relay_16a_1p = bit(register(40088), 9)
    """The 16 A single-phase relay of the type F socket is closed."""
    relay_16a_3p = bit(register(40088), 10)
    relay_32a_3p = bit(register(40088), 11)
    one_phase = bit(register(40088), 12, writable=True)
    """Charge on one phase; unset charges on three."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the wallbox and the lock its bit writes share."""
        super().__init__(*args, **kwargs)
        self._write_lock = asyncio.Lock()

    @property
    def slot(self) -> int:
        """The wallbox slot, from 0 to 7, that this control word belongs to."""
        return self._instance_offset

    @property
    def charging_allowed(self) -> bool | None:
        """Charging is allowed, the inverse of ``charging_aborted``."""
        aborted = self.charging_aborted
        return None if aborted is None else not aborted

    async def _async_write_bit(self, field: str, value: bool) -> None:
        """Set one bit, one read-modify-write of the word at a time."""
        if not isinstance(value, bool):
            raise InvalidValueError(f"{field} {value!r} is not a bool")
        async with self._write_lock:
            try:
                await self.write(field, value)
            except ModbusError as err:
                err.add_note(f"writing {field} of wallbox {self.slot}")
                raise

    async def async_write_solar_mode(self, enabled: bool) -> None:
        """Charge from solar only, or in hybrid mode."""
        await self._async_write_bit("solar_mode", enabled)

    async def async_write_charging_allowed(self, allowed: bool) -> None:
        """Allow charging, or abort it, through ``charging_aborted``."""
        if not isinstance(allowed, bool):
            raise InvalidValueError(f"charging_allowed {allowed!r} is not a bool")
        await self._async_write_bit("charging_aborted", not allowed)

    async def async_write_schuko_on(self, on: bool) -> None:
        """Switch the type F socket on or off, on a Wallbox classic only."""
        await self._async_write_bit("schuko_on", on)

    async def async_write_one_phase(self, enabled: bool) -> None:
        """Charge on one phase, or on three."""
        await self._async_write_bit("one_phase", enabled)


class Wallboxes(E3dcComponent):
    """The eight wallbox slots, in the order the device configuration sets."""

    slots = repeating_group(WALLBOX_COUNT, Wallbox, stride=1)

    @property
    def configured(self) -> dict[int, Wallbox]:
        """The configured wallboxes, keyed by slot."""
        return {
            number: wallbox
            for number, wallbox in enumerate(self.slots)
            if wallbox.available
        }
