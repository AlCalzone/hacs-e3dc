"""The top-level object for an E3/DC home power station."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from modbus_connection import (
    IllegalDataAddressError,
    IllegalFunctionError,
    ModbusExceptionError,
    ModbusTimeoutError,
)
from modbus_connection.model import Component, Device, Raw, read_optional

from .control import (
    BatteryControl,
    BatteryLimits,
    ControlState,
    GridControl,
    GridStatus,
    InverterModeControl,
)
from .enums import PowerMeterType
from .errors import NotAnE3dcError
from .identity import Identity
from .inverter import INVERTER_COUNT, INVERTER_STRIDE, Inverter, Inverters
from .meters import PowerMeters, WidePowerMeters
from .model import UpdateReport
from .power import DcStrings, PowerFlows
from .wallbox import Wallboxes

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_LOGGER = logging.getLogger(__name__)

# The manual allows at most 10 requests per second.
MESSAGE_SPACING = 0.1

# The 32-bit meters come before the 16-bit ones, so that both are marked
# fresh when the 16-bit block's listeners run.
READINGS = (
    "power",
    "dc_strings",
    "wallboxes",
    "wide_power_meters",
    "power_meters",
    "grid_status",
    "inverter_mode_control",
    "battery_limits",
    "inverters",
)
SETTINGS = ("grid_control", "battery_control")


@dataclass(frozen=True, slots=True)
class PowerMeterReading:
    """One meter slot, with the 32-bit phase powers where the device has them."""

    number: int
    meter_type: PowerMeterType | None
    power_l1: int | None
    power_l2: int | None
    power_l3: int | None

    @property
    def is_installed(self) -> bool:
        """Whether a meter is configured in this slot."""
        return self.meter_type not in (None, PowerMeterType.NOT_USED)


class E3dcPowerStation(Device):
    """An E3/DC home power station in Simple Mode, reached through a ``ModbusUnit``."""

    def __init__(self, unit: ModbusUnit, *, timeout: float | None = None) -> None:
        """Set up the sub-systems."""
        super().__init__(unit)
        if timeout is not None:
            unit.require_timeout(timeout)
        unit.set_message_spacing(MESSAGE_SPACING)

        self.identity = Identity(unit)
        self.power = PowerFlows(unit)
        self.dc_strings = DcStrings(unit)
        self.wallboxes = Wallboxes(unit)
        self.power_meters = PowerMeters(unit)

        # Filled in by setup when this device's firmware serves them.
        self.wide_power_meters: WidePowerMeters | None = None
        self.grid_status: GridStatus | None = None
        self.grid_control: GridControl | None = None
        self.battery_control: BatteryControl | None = None
        self.inverter_mode_control: InverterModeControl | None = None
        self.battery_limits: BatteryLimits | None = None
        self.inverters: Inverters | None = None

        self._setup_lock = asyncio.Lock()
        self._fresh_meter_blocks: set[str] = set()
        self._control_state = ControlState()
        self._stop_watching_connection = unit.on_connection_lost(self._forget_setpoints)
        self._note_fresh_on_update(self.power_meters, "power_meters")

    @classmethod
    async def async_detect(
        cls, unit: ModbusUnit, *, timeout: float | None = None
    ) -> Self:
        """Build the device, read its identity and settle which blocks it serves.

        Raises ``NotAnE3dcError`` when the device is not in E3DC mode.
        """
        device = cls(unit, timeout=timeout)
        await device._async_read_identity()
        await device.async_ensure_setup()
        return device

    def close(self) -> None:
        """Stop listening to the unit's connection, for a device no longer used."""
        self._stop_watching_connection()

    @property
    def serial_number(self) -> str | None:
        """The serial number, once the identity block has been read."""
        return self.identity.serial_number

    @property
    def model(self) -> str | None:
        """The model name, once the identity block has been read."""
        return self.identity.model

    @property
    def readings_components(self) -> tuple[str, ...]:
        """Reading component names this device polls; partial before setup."""
        return tuple(name for name in READINGS if getattr(self, name) is not None)

    @property
    def settings_components(self) -> tuple[str, ...]:
        """Settings component names this device polls; empty before setup."""
        return tuple(name for name in SETTINGS if getattr(self, name) is not None)

    @property
    def power_meter_readings(self) -> dict[int, PowerMeterReading]:
        """All eight meter slots, keyed by slot.

        The 32-bit phase powers win only when they refreshed in the last poll
        that read them. The 16-bit values and the meter types are from the
        last poll the 16-bit block answered.
        """
        wide_fresh = "wide_power_meters" in self._fresh_meter_blocks
        readings: dict[int, PowerMeterReading] = {}
        for number, narrow in enumerate(self.power_meters.meters):
            powers = (narrow.power_l1, narrow.power_l2, narrow.power_l3)
            if self.wide_power_meters is not None and wide_fresh:
                wide = self.wide_power_meters.meters[number]
                wide_powers = (wide.power_l1, wide.power_l2, wide.power_l3)
                if None not in wide_powers:
                    powers = wide_powers
            readings[number] = PowerMeterReading(number, narrow.meter_type, *powers)
        return readings

    async def async_ensure_setup(self) -> None:
        """Run setup once, even when several polls start at the same time."""
        async with self._setup_lock:
            await super().async_ensure_setup()

    async def _async_setup(self) -> None:
        """Check the magic value and settle which optional blocks the firmware has."""
        if self.identity.magic is None:
            await self._async_read_identity()
        elif not self.identity.is_e3dc:
            raise NotAnE3dcError(self.identity.magic)

        unit = self.modbus_unit
        self.wide_power_meters = await self._async_probe(WidePowerMeters(unit))
        if self.wide_power_meters is not None:
            self._fresh_meter_blocks.add("wide_power_meters")
            self._note_fresh_on_update(self.wide_power_meters, "wide_power_meters")
        self.grid_status = await self._async_probe(GridStatus(unit))
        # The control components share one state, which must be in place
        # before the probe reads the feed-in limit into it
        grid_control = GridControl(unit)
        battery_control = BatteryControl(unit)
        battery_limits = BatteryLimits(unit)
        for component in (grid_control, battery_control, battery_limits):
            component.state = self._control_state
        self.grid_control = await self._async_probe(grid_control)
        self.battery_control = await self._async_probe(battery_control)
        self.inverter_mode_control = await self._async_probe(InverterModeControl(unit))
        self.battery_limits = await self._async_probe(battery_limits)
        await self._async_find_inverters()
        _LOGGER.debug(
            "Power station %s, model %s, firmware %s; readings: %s; settings: %s",
            self.serial_number,
            self.model,
            self.identity.firmware_release,
            ", ".join(self.readings_components),
            ", ".join(self.settings_components) or "nothing",
        )

    async def _async_probe[C: Component](self, component: C) -> C | None:
        """Read an optional block once, or log that the firmware lacks it."""
        if (found := await read_optional(component)) is None:
            ranges = ", ".join(
                f"{low + 1}-{high + 1}" for low, high in component.register_ranges or ()
            )
            _LOGGER.info(
                "Power station %s refuses %s (registers %s), so it is not polled",
                self.serial_number,
                type(component).__name__,
                ranges or "as declared",
            )
        return found

    async def _async_read_identity(self) -> None:
        """Read the identity block, or raise ``NotAnE3dcError`` for a foreign device."""
        try:
            await self.identity.async_update(notify=False)
        except (IllegalDataAddressError, IllegalFunctionError) as err:
            # A device in SunSpec mode refuses the identity block
            raise NotAnE3dcError(None) from err
        if not self.identity.is_e3dc:
            raise NotAnE3dcError(self.identity.magic)

    async def _async_find_inverters(self) -> None:
        """Keep every inverter slot that answers its block.

        A slot without an inverter may answer with zeros. ``is_connected``
        tells the two apart once the inverter reports a grid voltage. Once
        slot 0 has answered, any refusal or timeout of an additional slot
        means that slot is absent until a new ``E3dcPowerStation`` sets up.
        """
        found: dict[int, Inverter] = {}
        for number in range(INVERTER_COUNT):
            inverter = Inverter(self.modbus_unit, base_offset=number * INVERTER_STRIDE)
            absent: tuple[type[Exception], ...] = (
                (IllegalDataAddressError, IllegalFunctionError)
                if not found
                else (ModbusExceptionError, ModbusTimeoutError)
            )
            try:
                await inverter.async_update()
            except absent as err:
                _LOGGER.info(
                    "Power station %s refuses inverter slot %d (registers %d-%d): %s",
                    self.serial_number,
                    number,
                    41000 + number * INVERTER_STRIDE,
                    41000 + number * INVERTER_STRIDE + INVERTER_STRIDE - 1,
                    err,
                )
                continue
            found[number] = inverter
        self.inverters = Inverters(self.modbus_unit, found) if found else None

    def _forget_setpoints(self) -> None:
        """Forget every setpoint, because the device may have dropped them."""
        self._control_state.clear_battery()
        if self.grid_control is not None:
            self.grid_control.clear_setpoints()

    def _note_fresh_on_update(self, component: Component, name: str) -> None:
        """Mark a meter block fresh before any other listener of it runs."""
        component.add_update_listener(lambda: self._fresh_meter_blocks.add(name))

    async def async_poll(
        self, names: Iterable[str], report: UpdateReport | None = None
    ) -> UpdateReport:
        """Poll ``names``, forgetting first which meter blocks were fresh."""
        names = tuple(names)
        self._fresh_meter_blocks -= {"power_meters", "wide_power_meters"} & set(names)
        return await super().async_poll(names, report)

    async def async_update_readings(self) -> UpdateReport:
        """Refresh what the device measures and reports on its own.

        That is the power flows, DC strings, wallboxes, power meters, the power
        output and inverter mode status, the battery limits and the inverters.
        """
        await self.async_ensure_setup()
        return await self.async_poll(READINGS)

    async def async_update_settings(self) -> UpdateReport:
        """Refresh the feed-in limit, the failsafe timeouts and the 40199 flag.

        These change only when something writes them.
        """
        await self.async_ensure_setup()
        return await self.async_poll(SETTINGS)

    async def async_update(self) -> UpdateReport:
        """Refresh readings and settings together in one report."""
        await self.async_ensure_setup()
        return await self.async_poll((*READINGS, *SETTINGS))

    async def async_read_raw(self, names: Iterable[str] | None = None) -> Raw:
        """Every register this device reads, undecoded, for diagnostics."""
        if names is None:
            names = ("identity", *READINGS, *SETTINGS)
        return await super().async_read_raw(names)
