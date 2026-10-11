"""Schedule the library's setpoint holds and translate their errors."""

import asyncio
from collections.abc import Awaitable
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from modbus_connection import ModbusError

from .const import DOMAIN
from .e3dc_ess_modbus import (
    E3dcError,
    E3dcPowerStation,
    Hold,
    HoldsClosedError,
    InconsistentWriteError,
    NoSetpointError,
    Setpoint,
    SetpointHolds,
)

REWRITE_INTERVAL = timedelta(seconds=10)

_INCONSISTENT_KEYS = {
    Setpoint.BATTERY_POWER: "battery_power_inconsistent",
    Setpoint.FEED_IN_LIMIT: "feed_in_limit_inconsistent",
}


class E3dcHolds:
    """The setpoints held for one power station, rewritten on Home Assistant's clock."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device: E3dcPowerStation
    ) -> None:
        """Initialize the holds and start the rewrite schedule."""
        self._hass = hass
        self._entry = entry
        self._holds = SetpointHolds(device, interval=None, on_change=self._changed)
        self._listeners: list[CALLBACK_TYPE] = []
        self._tick: asyncio.Task[None] | None = None
        self._unsub_interval: CALLBACK_TYPE | None = async_track_time_interval(
            hass, self._async_on_interval, REWRITE_INTERVAL
        )
        entry.async_on_unload(self._async_stop_timer)

    def get(self, setpoint: Setpoint) -> Hold | None:
        """The hold of ``setpoint``, if one is active or still being handed back."""
        return self._holds.get(setpoint)

    def requested(self, setpoint: Setpoint) -> float | None:
        """The last value written or remembered for ``setpoint``."""
        return self._holds.requested(setpoint)

    def remember(self, setpoint: Setpoint, value: float) -> None:
        """Remember a value to resume, without writing it."""
        self._holds.remember(setpoint, value)

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener`` whenever a hold changes; return the unsubscribe."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_start(self, setpoint: Setpoint, value: float) -> None:
        """Write and hold ``value``."""
        await self._async_translate(setpoint, self._holds.async_start(setpoint, value))

    async def async_resume(self, setpoint: Setpoint) -> None:
        """Hold the last value set again."""
        await self._async_translate(setpoint, self._holds.async_resume(setpoint))

    async def async_stop(self, setpoint: Setpoint) -> None:
        """Hand ``setpoint`` back."""
        await self._async_translate(setpoint, self._holds.async_stop(setpoint))

    async def async_close(self) -> None:
        """Stop rewriting and hand every setpoint back, for unload or shutdown.

        The library bounds the wait for a running rewrite and the hand-backs.
        """
        self._async_stop_timer()
        await self._holds.async_close()
        if self._tick is not None:
            # A rewrite still stuck on the link after the bound gives up too
            self._tick.cancel()
            await asyncio.gather(self._tick, return_exceptions=True)

    @callback
    def _async_stop_timer(self) -> None:
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    async def _async_translate(self, setpoint: Setpoint, call: Awaitable[None]) -> None:
        """Run a hold call, turning library errors into Home Assistant errors."""
        try:
            await call
        except HoldsClosedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            ) from err
        except NoSetpointError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_setpoint"
            ) from err
        except InconsistentWriteError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=_INCONSISTENT_KEYS.get(setpoint, "write_failed"),
            ) from err
        except (ModbusError, E3dcError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="write_failed"
            ) from err

    @callback
    def _async_on_interval(self, now: datetime) -> None:
        """Start a rewrite, unless the last one is still running."""
        if self._tick is not None and not self._tick.done():
            return
        self._tick = self._entry.async_create_background_task(
            self._hass, self._holds.async_rewrite(), "e3dc setpoint rewrite"
        )

    def _changed(self) -> None:
        for listener in list(self._listeners):
            listener()
