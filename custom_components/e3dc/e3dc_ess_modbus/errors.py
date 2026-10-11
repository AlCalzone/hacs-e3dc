"""The errors the library raises itself.

Transport and device errors arrive as modbus-connection's ``ModbusError``.
"""

from __future__ import annotations


class E3dcError(Exception):
    """The base of the errors this library raises itself."""


class NotAnE3dcError(E3dcError):
    """The device answered, but not as an E3/DC power station in E3DC mode."""

    def __init__(self, magic: int | None) -> None:
        """Initialize the error."""
        shown = "nothing" if magic is None else f"0x{magic:04X}"
        super().__init__(
            f"register 40001 holds {shown}, not 0xE3DC: "
            "is Modbus set to the E3/DC protocol?"
        )
        self.magic = magic


class InvalidValueError(E3dcError, ValueError):
    """A value to write is out of range or of the wrong type. Nothing was sent."""


class InconsistentWriteError(E3dcError):
    """A two-register setpoint failed after its first half may have landed.

    The library has tried to restore a safe state. For the battery power, 1
    went to 40199, which ends remote battery control. For the feed-in limit,
    the last limit written or read went back to 40186 and 40187. The cause
    is chained.
    """


class HoldsClosedError(E3dcError):
    """The setpoint holds were closed and take no new setpoints."""


class NoSetpointError(E3dcError):
    """There is no requested value to hold yet."""
