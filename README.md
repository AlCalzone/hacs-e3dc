# E3/DC for Home Assistant (HACS)

Connect an E3/DC home power station to Home Assistant over Modbus TCP. This is the custom integration build of the `e3dc` integration, for testing through [HACS](https://hacs.xyz) before the integration lands in Home Assistant itself.

It reads the power flows between solar, battery, house, grid and wallboxes, the power meters and inverters, and the energy management status. It can switch E3/DC wallboxes between solar and hybrid charging, charge or discharge the battery on command, and limit the feed-in and the inverter's output.

## Requirements

- Home Assistant 2026.10 or later. The integration shares its Modbus connection through Home Assistant's `modbus` integration, which gained that in 2026.9, and needs the modbus-connection release that 2026.10 ships.
- On the power station, under **Main menu** > **Smart Functions** > **Smart Home** > **Modbus**: Modbus turned on, with **Protocol** set to **E3DC**. SunSpec mode is not supported.
- Home Assistant in the same subnet as the power station. It answers no one else.

## Installation

1. In HACS, open the menu in the top right corner and choose **Custom repositories**.
2. Add `https://github.com/AlCalzone/hacs-e3dc` with the type **Integration**.
3. Find **E3/DC** in HACS, download it, and restart Home Assistant.
4. Go to **Settings** > **Devices & services**, select **Add integration**, and choose **E3/DC**.
5. Enter the power station's IP address and the Modbus port, `502` unless you changed it.

The integration checks that the device identifies as an E3/DC power station before it creates the entry.

## What you get

On the power station device:

- **PV power**, **Battery power**, **House power**, **Grid power**, **Additional feeder power**, **Wallbox power** and **Wallbox solar power**. Battery power is positive while charging. Grid power is positive while importing.
- **Battery** state of charge, **Self-sufficiency**, **Self-consumption** and **Emergency power** status.
- DC string power, voltage and current.
- Binary sensors for the energy management status: charging or discharging locked, emergency power ready, weather-based charging, feed-in limited, and the lockout periods.
- On newer firmware: stored energy, battery capacity and the charge and discharge limits.

A device per configured power meter, per inverter, and per wallbox. Each wallbox has switches for **Charging**, **Solar charging** and **Single-phase charging**.

On firmware that supports remote control, each setpoint is a number and a switch:

- **Battery power setpoint** and **Battery power control**: charge (positive) or discharge (negative) the battery at a fixed power.
- **Feed-in limit setpoint** and **Feed-in limit control**: cap the power fed into the grid.
- **Active power limit setpoint** and **Active power limit control**: cap the inverter's output as a percentage of its rated power.

Setting a number writes it and turns its switch on. The power station drops a setpoint 30 seconds after the last write, so while the switch is on, the integration writes it again every 10 seconds. Turning the switch off hands control back, and turning it on again holds the last value, also after a restart. A switch shows unknown while writing to the power station fails.

The integration has no energy counters, because the power station reports none over Modbus. For the Energy dashboard, create [Integral](https://www.home-assistant.io/integrations/integration/) helpers from the power sensors.

## Reporting problems

Open an [issue](https://github.com/AlCalzone/hacs-e3dc/issues) and attach the diagnostics download from the integration's device page. It contains the raw registers the integration read, minus the serial number. The register map comes from the manufacturer's Modbus manual, version 2.80, and has not been checked against every model and firmware, so a raw dump is what a wrong value is tracked down with.

To see what the integration does, turn on debug logging:

```yaml
logger:
  logs:
    custom_components.e3dc: debug
```

## Development

This repository follows [integration_blueprint](https://github.com/ludeeus/integration_blueprint). The code in `custom_components/e3dc` and the tests in `tests/e3dc` are generated, so fix bugs at their source:

- [e3dc-ess-modbus](https://github.com/AlCalzone/e3dc-ess-modbus) is the device library. It is vendored as `custom_components/e3dc/e3dc_ess_modbus`, so the integration needs no package from PyPI.
- [ha-core-e3dc](https://github.com/AlCalzone/ha-core-e3dc) is the integration as it is meant for Home Assistant core.

With both checked out next to this repository, rebuild and test:

```bash
scripts/setup
scripts/vendor.py
python3 -m pytest
```

`scripts/vendor.py` rewrites the library imports to relative ones, turns the manifest into a custom integration's, and resolves `strings.json` into `translations/en.json`. It keeps the version in the current manifest. `scripts/develop` starts a Home Assistant instance with the integration loaded, configured by `config/configuration.yaml`.

## License

Apache License 2.0
