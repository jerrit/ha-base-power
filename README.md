# Base Power - Home Assistant Integration (Unofficial)

[![GitHub Release](https://img.shields.io/github/v/release/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/releases)
[![HACS Validation](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml/badge.svg)](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/github/license/jerrit/ha-base-power?style=flat-square)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/stargazers)
[![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)

Unofficial Home Assistant integration for [Base Power](https://basepowercompany.com/) home battery systems. This project is not affiliated with or endorsed by Base Power.

> **25 kWh home battery** — Monitor your battery level, power usage, backup time, and energy consumption directly in Home Assistant.

## Features

- **Battery Level** — Estimated battery percentage derived from backup time
- **Backup Time** — Hours of backup power remaining
- **Current Power** — Real-time power consumption (watts)
- **Energy Monitoring** — 15-minute interval energy data compatible with HA Energy Dashboard
- **Daily Statistics** — Peak, low, and total daily energy consumption
- **Solar Status** — Whether solar panels are connected
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
5. Enter your Service Location ID (found in the Base Power app)

## Entities

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Battery Level | Estimated state of charge | % |
| Battery Backup Time | Hours of backup remaining | hours |
| Current Power | Current power draw | W |
| Current Interval Energy | Energy for current 15-min period | kWh |
| Daily Peak | Highest 15-min interval today | kWh |
| Daily Low | Lowest 15-min interval today | kWh |
| Daily Total Energy | Cumulative energy today | kWh |
| Intervals Today | Number of data intervals today | — |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Solar Connected | Whether solar panels are present |
| Battery Connected | Battery WiFi connectivity status |

## Energy Dashboard

The **Daily Total Energy** sensor uses `state_class: total_increasing` and is compatible with the Home Assistant Energy Dashboard. Add it as a grid consumption sensor.

## Technical Details

- **Protocol**: gRPC-Web over HTTPS (not standard gRPC)
- **Authentication**: Clerk (email OTP → JWT with 60-second lifetime)
- **Polling Interval**: 5 minutes (configurable)
- **Battery**: 25 kWh capacity system

## Troubleshooting

### "Authentication failed" during setup
- Ensure you're using the same email as your Base Power app account
- The verification code expires quickly — enter it promptly

### No data / sensors unavailable
- New installations may take time before telemetry data starts flowing
- Check that your battery is connected to WiFi in the Base Power app

### Reauthentication required
- Clerk sessions can expire. Go to the integration and click **Reconfigure** to re-authenticate.

## A Note on Cost & Billing

I have taken out cost & billing information, however if this is desired from others please put in an issue as a request and I can add it. Happy to receive any other feature enhancement requests as well!

## Contributing

Contributions are welcome! Please open an issue or PR on [GitHub](https://github.com/jerrit/ha-base-power).

## Disclaimer

This integration communicates with Base Power's private API, which may change without notice. Use at your own risk.

## License

MIT
