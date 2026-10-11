"""The top-level object for an E3/DC home power station."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from modbus_connection.model import ComponentGroup, Device, Raw, read_optional

from .control import BatteryControl, BatteryLimits, GridControl
from .enums import PowerMeterType
from .identity import Identity
from .inverter import INVERTER_COUNT, INVERTER_STRIDE, Inverter
from .meters import POWER_METER_COUNT, PowerMeters, WidePowerMeters
from .model import UpdateReport
from .power import DcString, PowerFlows
from .wallbox import Wallboxes

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_LOGGER = logging.getLogger(__name__)

# The manual allows at most 10 requests per second.
MESSAGE_SPACING = 0.1

DC_STRING_COUNT = 3

READINGS = (
    "power",
    "dc_strings",
    "wallboxes",
    "power_meters",
    "wide_power_meters",
    "battery_limits",
    "inverters",
)
SETTINGS = ("grid_control", "battery_control")


class NotAnE3dcError(Exception):
    """The device answered, but register 40001 does not hold the E3/DC magic."""

    def __init__(self, magic: int | None) -> None:
        """Initialize the error."""
        shown = "nothing" if magic is None else f"0x{magic:04X}"
        super().__init__(
            f"register 40001 holds {shown}, not 0xE3DC: "
            "is Modbus set to the E3/DC protocol?"
        )
        self.magic = magic


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
        unit.require_timeout(timeout)
        unit.set_message_spacing(MESSAGE_SPACING)

        self.identity = Identity(unit)
        self.power = PowerFlows(unit)
        self.dc_string_list = [
            DcString(unit, index=number) for number in range(1, DC_STRING_COUNT + 1)
        ]
        # The three strings' registers interleave, so they read as one.
        self.dc_strings = ComponentGroup(unit, self.dc_string_list)
        self.wallboxes = Wallboxes(unit)
        self.power_meters = PowerMeters(unit)

        # Filled in by setup when this device's firmware serves them.
        self.wide_power_meters: WidePowerMeters | None = None
        self.grid_control: GridControl | None = None
        self.battery_control: BatteryControl | None = None
        self.battery_limits: BatteryLimits | None = None
        self.inverter_slots: dict[int, Inverter] = {}
        self.inverters: ComponentGroup | None = None

    @classmethod
    async def async_detect(
        cls, unit: ModbusUnit, *, timeout: float | None = None
    ) -> Self:
        """Read the identity block and build the device it describes.

        Raises ``NotAnE3dcError`` when register 40001 lacks the magic value.
        """
        device = cls(unit, timeout=timeout)
        await device.identity.async_update(notify=False)
        if not device.identity.is_e3dc:
            raise NotAnE3dcError(device.identity.magic)
        return device

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

    def power_meter(self, number: int) -> PowerMeterReading:
        """Meter slot ``number``, from 0 to 7."""
        if not 0 <= number < POWER_METER_COUNT:
            raise ValueError(f"no power meter {number}")
        narrow = self.power_meters.meters[number]
        powers = (narrow.power_l1, narrow.power_l2, narrow.power_l3)
        if self.wide_power_meters is not None:
            wide = self.wide_power_meters.meters[number]
            wide_powers = (wide.power_l1, wide.power_l2, wide.power_l3)
            if None not in wide_powers:
                powers = wide_powers
        return PowerMeterReading(number, narrow.meter_type, *powers)

    async def _async_setup(self) -> None:
        """Check the magic value and settle which optional blocks the firmware has."""
        if self.identity.magic is None:
            await self.identity.async_update(notify=False)
        if not self.identity.is_e3dc:
            raise NotAnE3dcError(self.identity.magic)

        unit = self.modbus_unit
        self.wide_power_meters = await read_optional(WidePowerMeters(unit))
        self.grid_control = await read_optional(GridControl(unit))
        self.battery_control = await read_optional(BatteryControl(unit))
        self.battery_limits = await read_optional(BatteryLimits(unit))
        await self._async_find_inverters()
        _LOGGER.debug(
            "Power station %s, model %s, firmware %s; readings: %s; settings: %s",
            self.serial_number,
            self.model,
            self.identity.firmware_release,
            ", ".join(self.readings_components),
            ", ".join(self.settings_components) or "nothing",
        )

    async def _async_find_inverters(self) -> None:
        """Keep the own inverter and each additional one that reports a grid voltage."""
        found: dict[int, Inverter] = {}
        for number in range(INVERTER_COUNT):
            inverter = Inverter(self.modbus_unit, base_offset=number * INVERTER_STRIDE)
            if await read_optional(inverter) is None:
                _LOGGER.debug("Inverter %d refuses its block", number)
                continue
            if number == 0 or inverter.is_connected:
                found[number] = inverter
        self.inverter_slots = found
        self.inverters = (
            ComponentGroup(self.modbus_unit, list(found.values())) if found else None
        )

    async def async_update_readings(self) -> UpdateReport:
        """Refresh what the device measures: power, battery, wallboxes, meters."""
        await self.async_ensure_setup()
        return await self.async_poll(READINGS)

    async def async_update_settings(self) -> UpdateReport:
        """Refresh the remote control registers, which change only when written."""
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
