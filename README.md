# Base Power - Home Assistant Integration (Unofficial)

[![GitHub Release](https://img.shields.io/github/v/release/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/releases)
[![HACS Validation](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml/badge.svg)](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/github/license/jerrit/ha-base-power?style=flat-square)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/stargazers)
[![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)

Unofficial Home Assistant integration for [Base Power](https://basepowercompany.com/) home battery systems. This project is not affiliated with or endorsed by Base Power.

> Monitor your Base Power battery level, power usage, backup time, and energy consumption directly in Home Assistant. Supports single and dual battery configurations (25 kWh per unit).

## Features

- **Battery Level** — Real SoC% from the GridStatus API (15-min resolution)
- **Backup Time** — Dynamic estimate: `(SoC% × capacity kWh) ÷ avg home power kW`
- **System Capacity** — Auto-detected from API (25 kWh × battery count)
- **Current Power** — Real-time power consumption (watts)
- **Grid Power** — Current draw in amps from the latest 15-min grid reading
- **Energy Monitoring** — 15-minute interval energy data compatible with HA Energy Dashboard
- **Energy Breakdown** — Grid-to-home, solar-to-home, and battery-to-home energy
- **Total Home Energy** — Combined consumption from all sources (grid + solar + battery)
- **Self-Sufficiency** — % of home energy supplied by solar + battery vs grid
- **Daily Statistics** — Peak, low, total, and average interval energy
- **Daily Total (Grid)** — Total kWh from the grid status summary
- **Grid Status** — Binary sensor for grid power availability (outage detection)
- **Battery Status** — Operating state (Installed, In Service, etc.)
- **Battery Charging** — Whether the battery is currently charging or discharging
- **Solar Status** — Whether solar panels are connected
- **WiFi Diagnostics** — Signal strength and SSID of the battery's WiFi connection
- **Billing** — Current bill amount and due date
- **Battery Connectivity** — WiFi connection status

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/jerrit/ha-base-power` with category **Integration**
4. Click **Install**
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/base_power` folder to your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Base Power"
3. Enter your Base Power account email
4. Check your email for a verification code and enter it
5. Your account is configured automatically — no Service Location ID required

## Entities

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Battery Level | State of charge | % |
| Battery Backup Time | Hours of backup remaining | hours |
| Battery Count | Number of battery units detected | — |
| Battery Status | Operating state (Installed, In Service, etc.) | — |
| System Capacity | Total capacity (count × 25 kWh) | kWh |
| Current Power | Current power draw | W |
| Current Interval Energy | Energy for current 15-min period | kWh |
| Daily Peak | Highest 15-min interval today | kWh |
| Daily Low | Lowest 15-min interval today | kWh |
| Daily Total Energy | Cumulative energy today | kWh |
| Intervals Today | Number of data intervals today | — |
| Grid to Home Energy | Energy drawn from grid | kWh |
| Solar to Home Energy | Energy from solar panels | kWh |
| Battery to Home Energy | Energy discharged from battery | kWh |
| Total Home Energy | Combined home consumption (grid + solar + battery) | kWh |
| Self-Sufficiency | % of home energy from solar + battery | % |
| Grid Power (amps) | Current grid draw from latest 15-min reading | A |
| Daily Total (Grid) | Total grid kWh from grid status summary | kWh |
| Daily Average Grid Interval | Average kWh per 15-min grid interval | kWh |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Grid Power | Whether grid power is available (off = outage) |
| Solar Connected | Whether solar panels are present |
| Battery Connected | Battery WiFi connectivity status |
| Battery Charging | Whether battery SoC is currently increasing |

### Diagnostic Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| WiFi Signal | Battery WiFi signal strength | % |
| WiFi SSID | Connected WiFi network name | — |
| Bill Amount | Current billing amount | $ |
| Bill Due Date | Next bill due date | — |
| Asset ID | System asset identifier | — |

## Energy Dashboard

The following sensors use `state_class: total_increasing` and are ready to configure in **Settings → Energy**:

| Energy Dashboard slot | Sensor to use |
|----------------------|---------------|
| Grid consumption | Grid to Home Energy |
| Solar panels | Solar to Home Energy |
| Battery discharge | Battery to Home Energy |
| Home consumption | Total Home Energy |

## Technical Details

- **Protocol**: gRPC-Web over HTTPS (not standard gRPC)
- **Authentication**: Clerk (email OTP → JWT with 60-second lifetime, native mobile API pattern)
- **Polling Interval**: 5 minutes
- **Battery**: 25 kWh per unit, auto-detects 1 or 2 battery configurations
- **Battery %**: Real SoC% from `MobileGetGridStatus` 15-min time series
- **Backup Time**: Computed as `(SoC% × capacity kWh) ÷ avg home power kW` from today's usage intervals
- **Service Location ID**: Auto-discovered via `MobileGetAvailableLocations` — not required during setup

## Changelog

### v1.9.0
- Add **WiFi Network select entity** — a dropdown on the Base Power device card lets you choose a preferred WiFi network directly from Home Assistant, with available networks populated from a live scan sorted by signal strength
- Add **WiFi SSID dropdown in options** — the gear icon on the integration page now shows a live list of nearby networks with signal strength percentages instead of a plain text field; selecting "Auto (strongest signal)" clears the preference
- Add **battery unit count setting** — configure the number of battery units (1–10) via the gear icon on the integration page, useful when auto-detection doesn't match your actual hardware

### v1.8.0
- Fix battery count detection for 2-unit (50 kWh) systems — parser now counts repeated Field 3 sub-messages (one per battery unit) in addition to reading sub-field 1, so two-battery installs correctly report capacity and backup time
- Expanded protobuf debug logging: all unknown top-level fields are now logged with field number and value to aid future API reverse-engineering

### v1.7.0
- Auto-discover Service Location ID — setup is now email + OTP only, no manual ID lookup required
- Add Self-Sufficiency % sensor: `(solar + battery) ÷ total home consumption`
- Add Total Home Energy sensor: `grid + solar + battery` kWh (Energy Dashboard compatible)
- Add Battery Charging binary sensor: inferred from SoC delta between polls
- Add Daily Average Grid Interval sensor (already fetched, now exposed)
- Fix Solar Connected: falls back to `solar_to_home_kwh > 0` when API field is unset
- Fix Battery Backup Time: now a dynamic estimate from live SoC and average home load
- Energy Dashboard: add `suggested_display_precision` to all energy sensors

### v1.6.0
- Battery Level now shows real SoC% from `MobileGetGridStatus` (replaces self-calibration)
- Add Grid Power sensor: current draw in amps from the latest 15-min grid reading
- Add Daily Total (Grid) sensor: total kWh from the grid status summary
- Add parse support for rich `MobileGetGridStatus` response (battery SoC and power data)

### v1.5.0
- Add grid power outage detection binary sensor
- Add battery SoC from grid status API (replaces self-calibration when available)
- Add battery status enum sensor (Installed, In Service, etc.)
- Add WiFi signal strength and SSID diagnostic sensors
- Add energy breakdown sensors (grid-to-home, solar-to-home, battery-to-home)
- Add billing amount and due date diagnostic sensors
- Add asset ID diagnostic sensor
- Energy breakdown sensors are compatible with HA Energy Dashboard

### v1.4.0
- Remove device_class=ENERGY from non-cumulative sensors to fix HA state_class warnings
- Add debug logging for protobuf field identification
- Fix battery_count and has_solar field mappings

### v1.3.0
- Switch to native mobile API authentication pattern (`_is_native=1`)
- Add `x-mobile` header to match Clerk Expo SDK behavior
- Remove cookie-based auth in favor of Authorization header
- Extract session JWT directly from sign-in response when available
- Improved authentication reliability

### v1.2.0
- Dedicated Clerk session for auth (separate from HA's shared session)
- Token rotation tracking through full sign-in flow
- Debug logging for auth diagnostics

### v1.0.0
- Initial release with full sensor support
- Email OTP config flow
- gRPC-Web API integration
- Energy Dashboard compatible sensors

## Troubleshooting

### "Authentication failed" during setup
- Ensure you're using the same email as your Base Power app account
- The verification code expires quickly — enter it promptly

### No data / sensors unavailable
- New installations may take time before telemetry data starts flowing
- Check that your battery is connected to WiFi in the Base Power app

### Reauthentication required
- Clerk sessions can expire. Go to the integration and click **Reconfigure** to re-authenticate.

## Contributing

Contributions are welcome! Please open an issue or PR on [GitHub](https://github.com/jerrit/ha-base-power).

## Disclaimer

This integration communicates with Base Power's private API, which may change without notice. Use at your own risk.

## License

MIT
