# Base Power - Home Assistant Integration (Unofficial)

[![GitHub Release](https://img.shields.io/github/v/release/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/releases)
[![HACS Validation](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml/badge.svg)](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/github/license/jerrit/ha-base-power?style=flat-square)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/stargazers)
[![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)

Unofficial Home Assistant integration for [Base Power](https://basepowercompany.com/) home battery systems. This project is not affiliated with or endorsed by Base Power.

> Monitor your Base Power battery level, power usage, backup time, and energy consumption directly in Home Assistant. Supports 25, 40, 50, and 80 kWh systems.

## Features

- **Battery Level** — State of charge from `stateOfEnergyRaw`, updated about once a minute
- **Backup Time** — Hours of backup at the current home load
- **System Capacity** — 25, 40, 50, or 80 kWh, chosen in the integration options
- **Current Power** — Live home load in watts when the service context includes it
- **Grid Power** — On when grid voltage is at least 100 V and Base has not flagged an outage. Off during an outage or when voltage falls below 50 V. Unknown when that telemetry is missing
- **Energy Monitoring** — 15-minute interval energy from recent usage, for the Energy Dashboard
- **Daily Statistics** — Peak, low, total, and interval count from today's usage
- **Battery Status** — Operating state (Installed, In Service, etc.)
- **Battery Charging** — Off while state of charge stays full. Otherwise inferred from whether the state of charge is rising or falling
- **Solar Status** — Whether the dashboard reports solar
- **WiFi** — Choose the battery network. Signal strength is reported for that SSID only
- **Battery Connectivity** — Whether the gateway is connected
- **Billing** — Bill amount and due date when the account returns them

Grid to Home and Solar to Home sum today's hourly samples from `MobileGetUsageEnergy`. That call uses the numeric service location id. Battery to Home stays unknown. Recent usage, billing, cycles, and Wi-Fi keep the address string. Grid amps and the daily grid totals stay unknown: grid status is a voltage series.

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
| Battery Level | State of charge from the live service context | % |
| Battery Backup Time | Backup remaining at the current home load | hours |
| Battery Count | Units for the selected capacity | — |
| Battery Status | Operating state (Installed, In Service, etc.) | — |
| System Capacity | Capacity selected in the integration options | kWh |
| Current Power | Live home load, or the latest 15-minute average kW when live load is missing | W |
| Current Interval Energy | Energy for the current 15-min period (average kW ÷ 4) | kWh |
| Daily Peak | Highest 15-min interval today | kWh |
| Daily Low | Lowest 15-min interval today | kWh |
| Daily Total Energy | Today's 15-min intervals only, each average kW ÷ 4 | kWh |
| Intervals Today | Number of data intervals today | — |
| Grid to Home Energy | Energy drawn from grid | kWh |
| Solar to Home Energy | Energy from solar panels | kWh |
| Battery to Home Energy | Energy discharged from battery | kWh |
| Total Home Energy | Combined home consumption (grid + solar + battery) | kWh |
| Self-Sufficiency | % of home energy from solar + battery | % |
| Grid Power (amps) | Not reported by the current grid API | A |
| Daily Total (Grid) | Not reported by the current grid API | kWh |
| Daily Average Grid Interval | Not reported by the current grid API | kWh |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Grid Power | Grid supply is available. Attributes show `voltage_volts` and `active_outage` |
| Solar Connected | Whether solar panels are present |
| Battery Connected | Gateway is connected |
| Battery Charging | State of charge is increasing. Off while the battery stays full |

### Select

| Entity | Description |
|--------|-------------|
| WiFi Network | SSID to track. The list comes from the battery's Wi-Fi scan |

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

Grid to Home and Solar to Home fill from today's hourly samples. Battery to Home stays unknown. Recent usage fills Current Power, the daily totals, and the interval sensors. Daily totals count local-midnight onward, not the whole trailing 24 hours.

## Technical Details

- **Protocol**: gRPC-Web over HTTPS (not standard gRPC)
- **Authentication**: Clerk (email OTP → JWT with 60-second lifetime, native mobile API pattern)
- **Polling Interval**: Full refresh every 5 minutes. Grid voltage, outage state, battery percent, backup time, and current power every 60 seconds
- **Battery**: 25, 40, 50, or 80 kWh, set in the integration options. Base Core's nameplate is 39.2 kWh per unit; the integration uses 40 and 80.
- **Battery %**: `stateOfEnergyRaw` from `MobileGetServiceContext`
- **Backup Time**: `availableBackup` hours from `MobileGetServiceContext`, at the current home load
- **Grid voltage and outage**: `MobileGetGridStatus` and `MobileGetServiceContext`, called with the numeric service location id from the dashboard
- **Service Location ID**: Auto-discovered via `MobileGetAvailableLocations` — not required during setup. The address id and the numeric service location id are different values.

## Check this release before trusting it

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.

Confirm the sensors below after restarting Home Assistant. A passing check with the grid up does not prove the outage sensor.

- **Grid Power, grid up.** It should be **on**, with `voltage_volts` around the normal service voltage and `active_outage` false. Confirmed on the development account at about 245 V. The integration uses the numeric service location id from the dashboard. The address string still gets an empty body from these calls.
- **Grid Power, during an outage.** Still needs a test. Cut grid power at the panel, leave the house on the battery, and force an update. The sensor turns **off** when Base sets the active-outage flag, and also when voltage falls below 50 V. The September test stayed **on** because the request used the address string and the body came back empty. A missing response stays **unknown**. The sensor does not keep the previous "grid is up" reading.
- **Battery Level.** This reads `stateOfEnergyRaw`. The development account reported 100. The other state-of-energy field is not the percent.
- **Battery Backup Time.** This reads `availableBackup` hours and should move as the house load changes. The dashboard value 49652 is the numeric service location id, not a backup duration.
- **Current Power.** When service context includes home load, this sensor uses that live kilowatt reading. Otherwise the latest 15-minute sample is average kW, and watts are that value times 1000.
- **Battery Charging while full.** A flat 100% reads off. Confirmed on the development account.
- **Bill Amount.** The nested sample from issue #6 decodes to $188.53. Compare that with a real Base bill before treating the sensor as the amount due. The unmapped field next to it (33840) is not shown.
- **Grid to Home and Solar to Home.** `MobileGetUsageEnergy` uses the numeric service location id and no time fields. `UsageEnergy parsed` should show today's summed grid and solar kWh. Battery to Home stays unknown. A missing series stays unknown.
- **Daily Total Energy.** It sums 15-minute samples from local midnight onward. Each sample is average kW divided by 4. The previous 24-hour sum was about four times too large.
- **Daily grid total and grid amps.** Still unknown. Grid status is a voltage series.
- **Capacity.** Choose 25, 40, 50, or 80 kWh. Base Core is published as 39.2 kWh per unit and 78.4 kWh for two. There is no free-form kWh box and no automatic detection.

A separate Shiner app API was not investigated. Wi-Fi signal is reported for the SSID each user selects.

## Changelog

### Unreleased
- `MobileGetUsageEnergy` sends only the numeric service location id. The other usage, billing, cycle, and Wi-Fi calls keep the address string. The energy reply is an hourly series; today's grid and solar hours are summed. Battery energy stays unknown.
- Recent usage samples are average kW. Interval energy is that value divided by 4, watts are the value times 1000, and the daily total includes only the local day.
- Clerk session ids are not written to the log in full.

### v1.11.0
- Capacity choices are 25, 40, 50, and 80 kWh. Base Core's published size is 39.2 kWh per unit (78.4 for two); the options use those sizes rounded.
- Grid voltage, outage state, battery percent, and backup hours use the numeric service location id from the dashboard. `MobileGetGridStatus` and `MobileGetServiceContext` stay empty when called with the address string.
- Grid Power is on when the latest voltage is at least 100 V and Base has not set an outage. It is off when the outage flag is set, or when voltage is below 50 V. A missing response stays unknown.
- Battery Level reads `stateOfEnergyRaw`. Battery Backup Time reads `availableBackup` hours. Current Power uses the live home load when service context includes it.
- Battery Charging reports off while state of charge stays full.
- Bill amount is read from the nested billing message some accounts return.
- A missing usage-cycle contract is logged at debug instead of filling the error log every poll.
- Drop `aiohttp` from manifest requirements so hassfest passes. Home Assistant already provides it.
- Wi-Fi signal follows the SSID the user selects. The integration no longer substitutes the strongest network.

### v1.10.0
- Replace the battery unit-count field with a capacity choice: 25 kWh, 40 kWh, or 50 kWh. A 40 kWh single unit is no longer stored as 25 kWh. Existing integer settings keep working until the options are saved again.
- Poll grid status every 60 seconds so an outage does not wait for the 5-minute refresh. v1.11.0 uses that same poll for voltage, outage state, battery percent, backup time, and current power

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

## Thanks

- [@simonhogg](https://github.com/simonhogg) removed `aiohttp` from the manifest requirements and bumped the checkout action.
- [@universal9gsm](https://github.com/universal9gsm) reported the Core battery sizes and proposed a capacity override.
- [@yzfdave](https://github.com/yzfdave) asked for the 40 kWh Base Core option.
- [@jabone](https://github.com/jabone) fixed the nested bill amount, the grid sensor that defaulted to on, and charging while the battery stays full.
- [@christr](https://github.com/christr) cut power at the panel and showed that Grid Power stayed on.
- [@Sodium-Hydrogen](https://github.com/Sodium-Hydrogen) reported the usage-cycle error on every poll.

## Contributing

Contributions are welcome! Please open an issue or PR on [GitHub](https://github.com/jerrit/ha-base-power).

## Disclaimer

This integration communicates with Base Power's private API, which may change without notice. Use at your own risk.

## License

MIT
