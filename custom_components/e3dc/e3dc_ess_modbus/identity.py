"""The identification block, registers 40001 to 40067."""

from __future__ import annotations

from modbus_connection.model import bits, integer, string

from .model import E3dcComponent, register

MAGIC = 0xE3DC
"""What register 40001 holds on every E3/DC device in Simple Mode."""


class Identity(E3dcComponent):
    """Who made the device, what it is, and which firmware it runs."""

    magic = integer(register(40001), signed=False)
    """Always ``MAGIC``; anything else is a wrong address offset or mode."""
    _modbus_version_major = bits(register(40002), 8, 8)
    _modbus_version_minor = bits(register(40002), 0, 8)
    register_count = integer(register(40003), signed=False)
    """How many registers the device says it supports."""
    manufacturer = string(register(40004), 16)
    model = string(register(40020), 16)
    """For example ``S10 X`` or ``S20 X PRO``."""
    serial_number = string(register(40036), 16)
    """For example ``S10-12345678912``."""
    firmware_release = string(register(40052), 16)
    """For example ``S10_2024_02``, ``Q10_2024_02`` or ``P10_2024_02``."""

    @property
    def modbus_version(self) -> str | None:
        """The Modbus interface version, as ``major.minor``."""
        major, minor = self._modbus_version_major, self._modbus_version_minor
        if major is None or minor is None:
            return None
        return f"{major}.{minor}"

    @property
    def is_e3dc(self) -> bool:
        """Whether the magic register identifies an E3/DC device."""
        return self.magic == MAGIC
