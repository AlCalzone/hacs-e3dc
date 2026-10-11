"""Keep remote control setpoints alive against the device's 30 second watchdog.

The power station drops the battery power and the feed-in limit 30 seconds
after the last write, and the active power limit after its failsafe timeout.
``SetpointHolds`` writes a setpoint again on every rewrite until it is
stopped, and hands control back when it stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError

from .errors import (
    E3dcError,
    HoldsClosedError,
    InconsistentWriteError,
    InvalidValueError,
    NoSetpointError,
)

if TYPE_CHECKING:
    from .control import BatteryControl, GridControl
    from .device import E3dcPowerStation

_LOGGER = logging.getLogger(__name__)

REWRITE_INTERVAL = 10.0
"""Seconds between rewrites, well inside the 30 second watchdog."""
MAX_INTERVAL = 20.0
"""The longest interval that still leaves room for one failed rewrite."""
BATTERY_RELEASE_TIMEOUT = 30.0
"""Seconds after which a failed battery hand-back is moot: 40197 has lapsed."""
CLOSE_TIMEOUT = 5.0
"""Seconds ``async_close`` waits for the hand-backs."""


class Setpoint(StrEnum):
    """A setpoint the power station drops unless it is written again."""

    BATTERY_POWER = "battery_power"
    FEED_IN_LIMIT = "feed_in_limit"
    ACTIVE_POWER_LIMIT = "active_power_limit"


@dataclass(frozen=True)
class Hold:
    """A setpoint being written again, or still being handed back."""

    value: float
    failing: bool = False
    """The last rewrite failed, so the device may have dropped the setpoint."""
    releasing_since: float | None = None
    """When handing control back first failed; ``None`` while it hasn't."""

    @property
    def releasing(self) -> bool:
        """Whether handing control back failed and is being retried."""
        return self.releasing_since is not None

    @property
    def confirmed(self) -> bool:
        """Whether the last write to the device succeeded."""
        return not self.failing and not self.releasing


def _grid(station: E3dcPowerStation) -> GridControl:
    if station.grid_control is None:
        raise E3dcError("the firmware has no grid control")
    return station.grid_control


def _battery(station: E3dcPowerStation) -> BatteryControl:
    if station.battery_control is None:
        raise E3dcError("the firmware has no battery control")
    return station.battery_control


_WRITE: dict[Setpoint, Callable[[E3dcPowerStation, float], Awaitable[None]]] = {
    Setpoint.BATTERY_POWER: lambda station, value: _battery(
        station
    ).async_write_battery_power(int(value)),
    Setpoint.FEED_IN_LIMIT: lambda station, value: _grid(
        station
    ).async_write_feed_in_limit(int(value)),
    Setpoint.ACTIVE_POWER_LIMIT: lambda station, value: _grid(
        station
    ).async_write_active_power_limit(value),
}

# The feed-in limit has no hand-back: the station returns to its own limit
# 30 seconds after the last write. The active power limit is written to 100 %,
# because a failsafe timeout of 0 would keep it forever.
_RELEASE: dict[Setpoint, Callable[[E3dcPowerStation], Awaitable[None]]] = {
    Setpoint.BATTERY_POWER: lambda station: _battery(
        station
    ).async_stop_remote_control(),
    Setpoint.ACTIVE_POWER_LIMIT: lambda station: _grid(
        station
    ).async_write_active_power_limit(100),
}


class SetpointHolds:
    """The setpoints held on one power station.

    One lock serializes starting, stopping and rewriting, so a rewrite never
    writes a hold that was stopped or replaced while it waited. With an
    ``interval``, the holds rewrite on their own. With ``interval=None``,
    the caller schedules ``async_rewrite()`` itself, every 10 seconds or less.
    """

    def __init__(
        self,
        station: E3dcPowerStation,
        *,
        interval: float | None = REWRITE_INTERVAL,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the holds.

        Raises ``InvalidValueError`` for an interval outside 0 to 20 seconds.
        """
        if interval is not None and not 0 < interval <= MAX_INTERVAL:
            raise InvalidValueError(
                f"interval {interval} s is outside 0 to {MAX_INTERVAL} s"
            )
        self._station = station
        self._interval = interval
        self._on_change = on_change
        self._lock = asyncio.Lock()
        self._closed = False
        self._holds: dict[Setpoint, Hold] = {}
        self._requested: dict[Setpoint, float] = {}
        self._loop: asyncio.Task[None] | None = None

    def get(self, setpoint: Setpoint) -> Hold | None:
        """The hold of ``setpoint``, if one is active or still being handed back."""
        return self._holds.get(setpoint)

    def requested(self, setpoint: Setpoint) -> float | None:
        """The last value written or remembered for ``setpoint``, held or not."""
        return self._requested.get(setpoint)

    def remember(self, setpoint: Setpoint, value: float) -> None:
        """Remember ``value`` for ``async_resume`` without writing it."""
        self._requested[setpoint] = value

    async def async_start(self, setpoint: Setpoint, value: float) -> None:
        """Write ``value`` now and keep writing it until stopped.

        Raises ``HoldsClosedError`` after ``async_close``, and ``E3dcError``
        when the firmware lacks the setpoint's registers. Otherwise it raises
        whatever the write raises: ``InvalidValueError``,
        ``InconsistentWriteError`` or a ``ModbusError``. A failed write leaves
        the current hold as it was, except a half-written one, which ends it.
        """
        try:
            async with self._lock:
                if self._closed:
                    raise HoldsClosedError("the holds are closed")
                try:
                    await _WRITE[setpoint](self._station, value)
                except InconsistentWriteError:
                    self._holds.pop(setpoint, None)
                    raise
                self._requested[setpoint] = value
                self._holds[setpoint] = Hold(value)
                self._ensure_loop()
        finally:
            self._changed()

    async def async_resume(self, setpoint: Setpoint) -> None:
        """Hold the last requested value of ``setpoint`` again.

        Raises ``NoSetpointError`` if there is none.
        """
        if (value := self._requested.get(setpoint)) is None:
            raise NoSetpointError(f"no {setpoint} has been set yet")
        await self.async_start(setpoint, value)

    async def async_stop(self, setpoint: Setpoint) -> None:
        """Stop holding ``setpoint`` and hand control back.

        If handing back fails, this raises, the hold stays as releasing, and
        every rewrite retries it.
        """
        try:
            async with self._lock:
                await self._async_release(setpoint)
        finally:
            self._changed()

    async def async_close(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """Hand every setpoint back and take no new ones.

        Waiting for a running rewrite and the hand-backs together take at most
        ``timeout``. Afterwards nothing is held, whether the hand-backs
        succeeded or not, because nothing would retry them.
        """
        if self._loop is not None:
            self._loop.cancel()
        try:
            async with asyncio.timeout(timeout), self._lock:
                self._closed = True
                for setpoint in list(self._holds):
                    try:
                        await self._async_release(setpoint)
                    except (ModbusError, E3dcError) as err:
                        _LOGGER.warning("Could not release %s: %s", setpoint, err)
        except TimeoutError:
            _LOGGER.warning("Gave up handing setpoints back after %s s", timeout)
        self._closed = True
        self._holds.clear()
        if self._loop is not None:
            (result,) = await asyncio.gather(self._loop, return_exceptions=True)
            if isinstance(result, Exception):
                _LOGGER.error("The rewrite loop had failed: %r", result)
        self._changed()

    async def async_rewrite(self) -> None:
        """Write every held setpoint again, and retry failed hand-backs.

        A device refusing one setpoint doesn't stop the others. A connection
        error or a timeout ends the pass, so a dead link doesn't keep the lock
        for a timeout per setpoint. The setpoints it skipped count as failing.
        """
        changed = False
        async with self._lock:
            if self._closed:
                return
            pending = list(self._holds)
            while pending:
                setpoint = pending.pop(0)
                hold = self._holds[setpoint]
                try:
                    if hold.releasing:
                        changed |= await self._async_retry_release(setpoint, hold)
                    else:
                        changed |= await self._async_rewrite_one(setpoint, hold)
                except (ModbusConnectionError, ModbusTimeoutError):
                    for skipped in pending:
                        changed |= self._mark_failing(skipped, None)
                    break
                except ModbusError:
                    continue
        if changed:
            self._changed()

    def _mark_failing(self, setpoint: Setpoint, err: ModbusError | None) -> bool:
        """Mark a held setpoint failing; return whether that changed it."""
        hold = self._holds[setpoint]
        if hold.failing or hold.releasing:
            return False
        self._holds[setpoint] = replace(hold, failing=True)
        if err is None:
            _LOGGER.warning("Could not rewrite %s: the link is down", setpoint)
        else:
            _LOGGER.warning("Could not rewrite %s: %s", setpoint, err)
        return True

    async def _async_rewrite_one(self, setpoint: Setpoint, hold: Hold) -> bool:
        """Rewrite one hold; return whether its state changed.

        A ``ModbusError`` marks the hold failing and is raised on.
        """
        try:
            await _WRITE[setpoint](self._station, hold.value)
        except InconsistentWriteError as err:
            _LOGGER.warning(
                "Stopped holding %s after a half-written rewrite: %s", setpoint, err
            )
            self._holds.pop(setpoint, None)
            return True
        except ModbusError as err:
            if self._mark_failing(setpoint, err):
                self._changed()
            raise
        if hold.failing:
            self._holds[setpoint] = replace(hold, failing=False)
            _LOGGER.info("Rewriting %s works again", setpoint)
            return True
        return False

    async def _async_retry_release(self, setpoint: Setpoint, hold: Hold) -> bool:
        """Retry handing one setpoint back; return whether it was dropped."""
        assert hold.releasing_since is not None
        if (
            setpoint is Setpoint.BATTERY_POWER
            and time.monotonic() - hold.releasing_since >= BATTERY_RELEASE_TIMEOUT
        ):
            _LOGGER.info(
                "Gave up handing %s back: the power station dropped it by now", setpoint
            )
            self._holds.pop(setpoint, None)
            return True
        with contextlib.suppress(InconsistentWriteError):
            await self._async_release(setpoint)
            _LOGGER.info("Released %s after an earlier failure", setpoint)
            return True
        return False

    async def _async_release(self, setpoint: Setpoint) -> None:
        """Hand ``setpoint`` back; call only while holding the lock."""
        if (hold := self._holds.get(setpoint)) is None:
            return
        if (release := _RELEASE.get(setpoint)) is not None:
            try:
                await release(self._station)
            except (ModbusError, InconsistentWriteError):
                if hold.releasing_since is None:
                    self._holds[setpoint] = replace(
                        hold, releasing_since=time.monotonic()
                    )
                raise
        self._holds.pop(setpoint, None)

    def _ensure_loop(self) -> None:
        if self._interval is None or (self._loop is not None and not self._loop.done()):
            return
        self._loop = asyncio.create_task(self._async_loop(self._interval))

    async def _async_loop(self, interval: float) -> None:
        """Rewrite every ``interval`` seconds while anything is held."""
        while self._holds and not self._closed:
            await asyncio.sleep(interval)
            try:
                await self.async_rewrite()
            except Exception:
                _LOGGER.exception("A rewrite failed unexpectedly")

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            _LOGGER.exception("The on_change callback failed")
