"""The coded values of the E3/DC Simple Mode register map."""

from __future__ import annotations

from enum import IntEnum, IntFlag


class EmergencyPowerStatus(IntEnum):
    """Register 40084: the emergency power (backup) state."""

    NOT_SUPPORTED = 0
    """Older device generations, such as the S10-SP40 and S10-P5002."""
    ACTIVE = 1
    """The grid failed and the house runs on emergency power."""
    INACTIVE = 2
    NOT_AVAILABLE = 3
    MOTOR_SWITCH_OFF = 4
    """S10 E and S10 E PRO only: the motor switch was turned off or never on."""


class EmsStatus(IntFlag):
    """Register 40085: the energy management system's state bits."""

    CHARGING_LOCKED = 1 << 0
    DISCHARGING_LOCKED = 1 << 1
    EMERGENCY_POWER_READY = 1 << 2
    """Emergency power is available, once the batteries are charged."""
    WEATHER_BASED_CHARGING = 1 << 3
    """Charging capacity is held back for the sunshine the forecast expects."""
    FEED_IN_LIMITED = 1 << 4
    """Output is throttled because the maximum feed-in has been reached."""
    CHARGE_LOCK_TIME = 1 << 5
    """A charging lockout period configured under Smart Charge is active."""
    DISCHARGE_LOCK_TIME = 1 << 6
    """A discharge lockout period configured under Smart Charge is active."""


class PowerMeterType(IntEnum):
    """Registers 40105, 40109, ...: what a power meter slot measures."""

    ROOT = 1
    """The main meter at the building's grid connection point."""
    EXTERNAL_PRODUCTION = 2
    BIDIRECTIONAL = 3
    EXTERNAL_CONSUMPTION = 4
    FARM = 5
    NOT_USED = 6
    WALLBOX = 7
    EXTERNAL_FARM = 8
    DATA_DISPLAY = 9
    """Logged on the customer portal only, outside the control loop."""
    CONTROL_BYPASS = 10
    """Measured power that is neither charged into nor drawn from the battery."""


class InverterModeRequest(IntEnum):
    """Register 40200: the inverter operating mode to force."""

    DISABLE_OVERWRITE = 0
    FORCE_IDLE_OFF_GRID_POWER_SAVE = 10


class InverterMode(IntEnum):
    """Register 40201: the inverter operating mode in effect."""

    INIT = 0
    IDLE_OFF_GRID_POWER_SAVE = 10
