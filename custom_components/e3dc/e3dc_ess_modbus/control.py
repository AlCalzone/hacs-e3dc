"""Remote control of the grid connection and the battery, registers 40186 to 40210.

Several of these registers are write-only. The components read only the
readable ones and offer a method per write-only setpoint.

How long a setpoint holds depends on its register. The feed-in limit and the
battery power lapse 30 seconds after the last write. The reactive power
setpoints and the active power limit hold for the failsafe timeouts in 40194
and 40196, which default to 30 seconds. A timeout of 0 turns the failsafe off,
and the setpoint then holds until it is written again.

The library writes a setpoint once. A caller that wants it to hold writes it
again before its timeout runs out. A ``*_setpoint`` attribute holds the last
value written that the device acknowledged. It is not cleared when the
setpoint lapses, only when the connection drops.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

from modbus_connection import (
    IllegalFunctionError,
    ModbusExceptionError,
)
from modbus_connection.encode import encode_int
from modbus_connection.model import boolean, enum, int32, integer

from .enums import InverterMode, InverterModeRequest
from .errors import InconsistentWriteError, InvalidValueError
from .model import E3dcComponent, register

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_LOGGER = logging.getLogger(__name__)

INT16_MIN, INT16_MAX = -0x8000, 0x7FFF
INT32_MIN, INT32_MAX = -0x8000_0000, 0x7FFF_FFFF
COS_PHI_MIN, COS_PHI_MAX = -600, 600
PERCENT_MIN, PERCENT_MAX = -100, 100

# The manual's numbers of the setpoint registers
FEED_IN_LIMIT = 40186
REACTIVE_POWER = 40189
COS_PHI = 40190
REACTIVE_POWER_PERCENT = 40191
ACTIVE_POWER_LIMIT = 40192
ACTIVE_POWER_FAILSAFE = 40193
REACTIVE_POWER_FAILSAFE = 40195
BATTERY_POWER = 40197
REMOTE_CONTROL_DISABLED = 40199
BATTERY_POWER_PERCENT = 40210


@dataclass
class ControlState:
    """What the control components of one power station share."""

    fc16_refused: bool = False
    """The device refused FC16 as an illegal function, so int32 setpoints take
    two FC06 writes."""
    battery_power: int | None = None
    """The last battery power acknowledged, in W."""
    battery_power_percent: float | None = None
    """The last battery power percentage acknowledged."""
    feed_in_limit: int | None = None
    """The last feed-in limit written or read, in W."""
    feed_in_limit_unknown: bool = False
    """A failed write may have left 40186 and 40187 holding a mixed value."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    """Held across each battery and feed-in write, so they reach the device in order."""

    def clear_battery(self) -> None:
        """Forget the battery setpoint, after a stop or a failed write."""
        self.battery_power = None
        self.battery_power_percent = None


def _int(value: Any, low: int, high: int, what: str) -> int:
    """Check that ``value`` is an int from ``low`` to ``high``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidValueError(f"{what} {value!r} is not an int")
    if not low <= value <= high:
        raise InvalidValueError(f"{what} {value} is outside {low} to {high}")
    return value


def _percent(value: Any, low: float, high: float, what: str) -> float:
    """Check that ``value`` is a number from ``low`` to ``high``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidValueError(f"{what} {value!r} is not a number")
    if not low <= value <= high:
        raise InvalidValueError(f"{what} {value} % is outside {low} to {high}")
    return value


def _timeout(value: Any) -> int:
    return _int(value, 0, 0xFFFF, "timeout")


def _flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidValueError(f"remote_control_disabled {value!r} is not a bool")
    return value


def _mode_request(value: Any) -> InverterModeRequest:
    if not isinstance(value, InverterModeRequest):
        raise InvalidValueError(
            f"inverter_mode_request {value!r} is not an InverterModeRequest"
        )
    return value


def _word(value: int) -> int:
    """A checked signed 16-bit value as the register word that carries it."""
    return encode_int(value, count=1)[0]


async def _write_tenths(
    unit: ModbusUnit, index: int, percent: Any, low: float, high: float, what: str
) -> float:
    """Write a percentage to register ``index`` and return the value it encodes."""
    tenths = round(_percent(percent, low, high, what) * 10)
    await unit.write_register(register(index), _word(tenths))
    return tenths / 10


async def _write_int32(
    unit: ModbusUnit,
    state: ControlState,
    index: int,
    value: int,
    restore: Callable[[], Awaitable[None]],
    forget: Callable[[], None],
) -> None:
    """Write a checked signed 32-bit value to register ``index`` and the next one.

    Call only while holding ``state.lock``. The manual lists no FC16 for this
    device, but one FC16 write is the only way to change both words at once.
    A device that refuses FC16 as an illegal function gets two FC06 writes,
    low word first, for the rest of the session.

    A failure after the low word may have landed leaves the pair holding
    neither value. ``restore`` then runs, shielded from cancellation, before
    this raises ``InconsistentWriteError``, or re-raises a cancellation. A
    failed FC16 write that may have landed runs ``forget``, because the pair
    then holds either the old or the new value.
    """
    low, high = encode_int(value, count=2, word_order="little")
    if not state.fc16_refused:
        try:
            await unit.write_registers(register(index), [low, high])
        except IllegalFunctionError:
            _LOGGER.warning(
                "The power station refuses FC16, so two-register setpoints now "
                "take two single-register writes"
            )
            state.fc16_refused = True
        except ModbusExceptionError:
            raise  # an exception response means nothing landed
        except BaseException:
            forget()
            raise
        else:
            return
    try:
        await unit.write_register(register(index), low)
    except ModbusExceptionError:
        raise  # an exception response means the low word didn't land
    except BaseException as err:
        await _async_restore_and_raise(index, value, restore, err)
    try:
        await unit.write_register(register(index + 1), high)
    except BaseException as err:
        await _async_restore_and_raise(index, value, restore, err)


async def _async_restore_and_raise(
    index: int,
    value: int,
    restore: Callable[[], Awaitable[None]],
    err: BaseException,
) -> NoReturn:
    """Restore a safe state after a half-written pair, then raise."""
    _LOGGER.warning(
        "Writing %d to registers %d and %d failed halfway (%r): the pair may hold "
        "neither the old nor the new value, restoring a safe state",
        value,
        index,
        index + 1,
        err,
    )
    # The restore runs as its own task and must finish before the caller's
    # lock is released, even if this task is cancelled again while it waits
    task = asyncio.ensure_future(restore())
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as cancel:
            cancelled = cancel
        except Exception:  # noqa: BLE001, S110
            pass  # retrieved from the task below
    if not task.cancelled() and (restore_err := task.exception()) is not None:
        _LOGGER.warning("Restoring a safe state failed too: %r", restore_err)
    if cancelled is not None:
        raise cancelled
    if not isinstance(err, Exception):
        raise err
    raise InconsistentWriteError(
        f"writing {value} to registers {index} and {index + 1} failed halfway"
    ) from err


class GridControl(E3dcComponent):
    """Feed-in limitation and active and reactive power control, from 2025_06.

    The device takes these only when it is certified for grid operator control.
    """

    register_ranges = (
        (register(40186), register(40187)),
        (register(40194), register(40194)),
        (register(40196), register(40196)),
    )

    feed_in_limit = int32(register(40186), word_order="little", unit="W")
    """The last limit written, not the limit in effect."""
    active_power_failsafe_timeout = integer(
        register(40194), signed=False, unit="s", writable=_timeout
    )
    """How long an active power setpoint holds; 0 turns the failsafe off."""
    reactive_power_failsafe_timeout = integer(
        register(40196), signed=False, unit="s", writable=_timeout
    )
    """How long a reactive power setpoint holds; 0 turns the failsafe off."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the component and the setpoints it remembers."""
        super().__init__(*args, **kwargs)
        self.state = ControlState()
        self.add_update_listener(self._note_feed_in_limit)
        self.reactive_power_setpoint: int | None = None
        """The last reactive power acknowledged, in var."""
        self.cos_phi_setpoint: int | None = None
        """The last cos φ setpoint acknowledged, from -600 to 600."""
        self.reactive_power_percent_setpoint: float | None = None
        """The last reactive power acknowledged, in % of rated apparent power."""
        self.active_power_limit_setpoint: float | None = None
        """The last active power limit acknowledged, in %."""
        self.active_power_failsafe_setpoint: float | None = None
        """The last active power failsafe acknowledged, in %."""
        self.reactive_power_failsafe_setpoint: float | None = None
        """The last reactive power failsafe acknowledged, in %."""

    def _note_feed_in_limit(self) -> None:
        """Remember the feed-in limit read, which is the last one written.

        After a half-written pair with nothing to restore, the register holds a
        mixed value, so reads count again only once a write has succeeded.
        """
        if self.feed_in_limit is not None and not self.state.feed_in_limit_unknown:
            self.state.feed_in_limit = self.feed_in_limit

    def clear_setpoints(self) -> None:
        """Forget every setpoint, for example after the connection dropped."""
        self._set_reactive(None, None, None)
        self.active_power_limit_setpoint = None
        self.active_power_failsafe_setpoint = None
        self.reactive_power_failsafe_setpoint = None

    def _set_reactive(
        self, var: int | None, cos_phi: int | None, percent: float | None
    ) -> None:
        """Remember one way of setting the reactive power, forgetting the others."""
        self.reactive_power_setpoint = var
        self.cos_phi_setpoint = cos_phi
        self.reactive_power_percent_setpoint = percent

    async def async_write_feed_in_limit(self, watts: int) -> None:
        """Cap the power fed into the grid; rewrite within 30 seconds to keep it.

        If the write fails halfway, both words of the last limit written or
        read go back, high word first. Without one, the pair stays as it is
        until the limit lapses.
        """
        checked = _int(watts, 0, INT32_MAX, "feed_in_limit")
        async with self.state.lock:
            previous = self.state.feed_in_limit

            def forget() -> None:
                self.state.feed_in_limit = None
                self.state.feed_in_limit_unknown = True

            async def restore() -> None:
                forget()
                if previous is None:
                    return
                old_low, old_high = encode_int(previous, count=2, word_order="little")
                try:
                    await self._unit.write_register(
                        register(FEED_IN_LIMIT + 1), old_high
                    )
                finally:
                    # The high word may never have changed, so the low word
                    # alone can still bring the old limit back
                    await self._unit.write_register(register(FEED_IN_LIMIT), old_low)
                self.state.feed_in_limit = previous
                self.state.feed_in_limit_unknown = False

            await _write_int32(
                self._unit, self.state, FEED_IN_LIMIT, checked, restore, forget
            )
            self.state.feed_in_limit = checked
            self.state.feed_in_limit_unknown = False

    async def async_write_reactive_power(self, var: int) -> None:
        """Set the reactive power, in var.

        The manual allows ±50 % of the rated apparent power, which the device
        enforces. This checks only that the value fits the register.
        """
        checked = _int(var, INT16_MIN, INT16_MAX, "reactive_power")
        await self._unit.write_register(register(REACTIVE_POWER), _word(checked))
        self._set_reactive(checked, None, None)

    async def async_write_cos_phi(self, value: int) -> None:
        """Set the reactive power as cos φ, from -600 (0.4 capacitive) to 600."""
        checked = _int(value, COS_PHI_MIN, COS_PHI_MAX, "cos_phi")
        await self._unit.write_register(register(COS_PHI), _word(checked))
        self._set_reactive(None, checked, None)

    async def async_write_reactive_power_percent(self, percent: float) -> None:
        """Set the reactive power as a percentage of the rated apparent power."""
        checked = await _write_tenths(
            self._unit,
            REACTIVE_POWER_PERCENT,
            percent,
            PERCENT_MIN,
            PERCENT_MAX,
            "reactive_power_percent",
        )
        self._set_reactive(None, None, checked)

    async def async_write_active_power_limit(self, percent: float) -> None:
        """Cap the inverter's active power, ignoring the house's consumption."""
        self.active_power_limit_setpoint = await _write_tenths(
            self._unit,
            ACTIVE_POWER_LIMIT,
            percent,
            0,
            PERCENT_MAX,
            "active_power_limit",
        )

    async def async_write_active_power_failsafe(self, percent: float) -> None:
        """Set the active power limit that applies once a setpoint times out."""
        self.active_power_failsafe_setpoint = await _write_tenths(
            self._unit,
            ACTIVE_POWER_FAILSAFE,
            percent,
            0,
            PERCENT_MAX,
            "active_power_failsafe",
        )

    async def async_write_reactive_power_failsafe(self, percent: float) -> None:
        """Set the reactive power that applies once a setpoint times out."""
        self.reactive_power_failsafe_setpoint = await _write_tenths(
            self._unit,
            REACTIVE_POWER_FAILSAFE,
            percent,
            0,
            PERCENT_MAX,
            "reactive_power_failsafe",
        )

    async def async_write_active_power_failsafe_timeout(self, seconds: int) -> None:
        """Set how long an active power setpoint holds; 0 turns the failsafe off."""
        await self.write("active_power_failsafe_timeout", seconds)

    async def async_write_reactive_power_failsafe_timeout(self, seconds: int) -> None:
        """Set how long a reactive power setpoint holds; 0 turns the failsafe off."""
        await self.write("reactive_power_failsafe_timeout", seconds)


class GridStatus(E3dcComponent):
    """Whether the system can deliver power, from release 2025_06."""

    power_output_ready = boolean(register(40188))
    """PV inverter on the grid, DC-DC converter working, battery dischargeable."""


class BatteryControl(E3dcComponent):
    """Remote control of the battery power, from release 2025_40.

    On 2026_04 and later the battery power can also be set as a percentage,
    through ``BatteryLimits.async_write_battery_power_percent()``.
    """

    register_ranges = ((register(40199), register(40199)),)

    remote_control_disabled = boolean(register(40199), writable=_flag)
    """1 disables remote battery control at once. The manual says 0 has no effect."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the component."""
        super().__init__(*args, **kwargs)
        self.state = ControlState()

    @property
    def battery_power_setpoint(self) -> int | None:
        """The last battery power acknowledged, in W, or ``None`` after a stop."""
        return self.state.battery_power

    async def async_write_battery_power(self, watts: int) -> None:
        """Charge (positive) or discharge (negative) the battery at ``watts``.

        PV power charges first and the grid makes up the rest. The device
        returns to normal control unless this is rewritten within 30 seconds.
        If the write fails halfway, writing 1 to 40199 ends remote control.
        """
        checked = _int(watts, INT32_MIN, INT32_MAX, "battery_power")
        async with self.state.lock:

            async def restore() -> None:
                self.state.clear_battery()
                await self._unit.write_register(register(REMOTE_CONTROL_DISABLED), 1)

            await _write_int32(
                self._unit,
                self.state,
                BATTERY_POWER,
                checked,
                restore,
                self.state.clear_battery,
            )
            self.state.clear_battery()
            self.state.battery_power = checked

    async def async_stop_remote_control(self) -> None:
        """End remote battery control at once, without waiting for its timeout."""
        await self.write("remote_control_disabled", True)

    async def write(self, field: str, value: Any) -> None:
        """Write a field, keeping the battery setpoint in step with 40199."""
        async with self.state.lock:
            await super().write(field, value)
            if field == "remote_control_disabled" and value is True:
                self.state.clear_battery()

    async def async_write_remote_control_disabled(self, disabled: bool) -> None:
        """Write register 40199. True does what ``async_stop_remote_control`` does."""
        if _flag(disabled):
            await self.async_stop_remote_control()
        else:
            await self.write("remote_control_disabled", False)


class InverterModeControl(E3dcComponent):
    """The inverter operating mode, from release 2025_43."""

    register_ranges = ((register(40200), register(40201)),)

    inverter_mode_request = enum(
        register(40200), InverterModeRequest, signed=True, writable=_mode_request
    )
    inverter_mode = enum(register(40201), InverterMode, signed=True)
    """The mode in effect, which the device changes on its own."""

    async def async_write_inverter_mode_request(
        self, mode: InverterModeRequest
    ) -> None:
        """Force an inverter operating mode, or hand control back."""
        await self.write("inverter_mode_request", mode)


class BatteryLimits(E3dcComponent):
    """What the battery can take and give right now, from release 2026_04.

    It also carries the percentage battery write, which arrived with the same
    release. The watt write is ``BatteryControl.async_write_battery_power()``.
    """

    max_charge_power = int32(register(40202), word_order="little", unit="W")
    max_discharge_power = int32(register(40204), word_order="little", unit="W")
    """Negative."""
    remaining_energy = int32(register(40206), word_order="little", unit="Wh")
    full_charge_energy = int32(register(40208), word_order="little", unit="Wh")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the component."""
        super().__init__(*args, **kwargs)
        self.state = ControlState()

    @property
    def battery_power_percent_setpoint(self) -> float | None:
        """The last battery power percentage acknowledged, or ``None``."""
        return self.state.battery_power_percent

    async def async_write_battery_power_percent(self, percent: float) -> None:
        """Charge (positive) or discharge (negative) at a share of full power.

        The manual lists register 40210 with length 1 and type Int32. This
        writes one signed 16-bit register.
        """
        async with self.state.lock:
            checked = await _write_tenths(
                self._unit,
                BATTERY_POWER_PERCENT,
                percent,
                PERCENT_MIN,
                PERCENT_MAX,
                "battery_power_percent",
            )
            self.state.clear_battery()
            self.state.battery_power_percent = checked
