"""Wallbox control words, registers 40088 to 40095."""

from __future__ import annotations

from modbus_connection.model import bit, repeating_group

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
    charging = bit(register(40088), 3)
    type2_locked = bit(register(40088), 4)
    type2_plugged = bit(register(40088), 5)
    """Multi connect and efy wallboxes set this only with a vehicle attached."""
    schuko_on = bit(register(40088), 6, writable=True)
    """The type F (CEE 7/3) socket is switched on."""
    schuko_plugged = bit(register(40088), 7)
    schuko_locked = bit(register(40088), 8)
    relay_16a_1p = bit(register(40088), 9)
    """The 16 A single-phase relay of the type F socket is closed."""
    relay_16a_3p = bit(register(40088), 10)
    relay_32a_3p = bit(register(40088), 11)
    one_phase = bit(register(40088), 12, writable=True)
    """Charge on one phase; unset charges on three."""

    async def async_write_solar_mode(self, enabled: bool) -> None:
        """Charge from solar only, or in hybrid mode."""
        await self.write("solar_mode", enabled)

    async def async_write_charging(self, enabled: bool) -> None:
        """Allow charging, or abort it."""
        await self.write("charging_aborted", not enabled)

    async def async_write_one_phase(self, enabled: bool) -> None:
        """Charge on one phase, or on three."""
        await self.write("one_phase", enabled)


class Wallboxes(E3dcComponent):
    """The eight wallbox slots, in the order the device configuration sets."""

    slots = repeating_group(WALLBOX_COUNT, Wallbox, stride=1)

    @property
    def available(self) -> list[tuple[int, Wallbox]]:
        """Each configured wallbox with its slot number."""
        return [
            (number, slot) for number, slot in enumerate(self.slots) if slot.available
        ]
