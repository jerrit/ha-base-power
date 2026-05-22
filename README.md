# Base Power for Home Assistant

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/jerrit/ha-base-power)](https://github.com/jerrit/ha-base-power/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An unofficial Home Assistant integration for **[Base Power](https://basepowercompany.com)** — bringing your home battery, solar, grid, and energy data directly into Home Assistant.

> **Disclaimer:** This is a community-built integration. It is not affiliated with, endorsed by, or supported by Base Power Company. Use at your own risk.

---

## What You Get

After setup, Home Assistant will have a **Base Power device** with:

### Sensors
| Entity | Description |
|--------|-------------|
| Current Interval Energy | Energy consumed in the current 15-min window (kWh) |
| Current Power | Instantaneous power estimate derived from the interval (W) |
| Daily Total Energy | Sum of all 15-min readings today (kWh) |
| Daily Peak Interval | Highest single 15-min reading today (kWh) |
| Intervals Today | Number of 15-min readings received today |
| Battery Status | Battery state (not_installed / installed / in_service / …) |
| Service State | Service state (unknown / in_service / …) |
| Grid to Home Energy | Energy drawn from the grid (kWh) |
| Solar to Home Energy | Energy sourced from solar (kWh) |
| Total Energy to Home | Combined energy delivered to the home (kWh) |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Grid Connected | Is the battery connected to the grid? |
| Outage Active | Is an outage currently in progress? |
| Battery Backup Active | Is the battery actively supplying backup power? |
| Solar Available | Does this system include solar? |
| Automatic Backup Enabled | Is automatic backup mode configured? |

### Buttons
| Entity | Description |
|--------|-------------|
| Trigger Manual Backup | Switches the battery to backup mode immediately |
| Reset Overcurrent Protection | Resets the overcurrent protection circuit |

---

## Requirements

- Home Assistant **2024.1.0** or newer
- A Base Power account (email + password / email OTP)
- HACS installed (recommended) **or** manual installation

---

## Installation

### Option A — HACS (Recommended)

1. Open HACS in your Home Assistant sidebar.
2. Click **Integrations** → **⋮ menu** → **Custom repositories**.
3. Add `https://github.com/jerrit/ha-base-power` with category **Integration**.
4. Search for **Base Power** in HACS and click **Download**.
5. Restart Home Assistant.

### Option B — Manual

1. Download the [latest release](https://github.com/jerrit/ha-base-power/releases).
2. Copy the `custom_components/base_power` folder into your HA config directory so the path is:
   ```
   /config/custom_components/base_power/
   ```
3. Restart Home Assistant.

---

## Configuration

Base Power uses **email one-time codes** for sign-in — no password required.

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Base Power** and select it.
3. **Step 1 — Email:** Enter the email address on your Base Power account and click **Submit**. A 6-digit code will be sent to that address.
4. **Step 2 — Verify:** Open the email from Base Power and enter the 6-digit code.
5. **Step 3 — Location** *(only shown if you have multiple service addresses)*: Select which location to monitor.
6. Done! Your Base Power device will appear in Home Assistant within a few seconds.

### Re-authentication

If your session expires, Home Assistant will show a notification prompting you to sign in again. Follow the same email OTP flow — no data will be lost.

---

## Data Update Frequency

| Data | Interval | Notes |
|------|----------|-------|
| Energy / battery status | Every 5 minutes | Primary dashboard data |
| Grid status | Every 5 minutes | Outage detection |
| Authentication token | Every 50 seconds | Background, transparent to the user |

> The Base Power API provides 15-minute interval readings. Polling more frequently than that will not yield new energy data.

---

## Energy Dashboard

To use **Current Interval Energy** or **Daily Total Energy** with the Home Assistant Energy Dashboard:

1. Go to **Settings → Dashboards → Energy**.
2. Under **Electricity grid → Grid consumption**, add **Base Power Current Interval Energy**.
3. If you have solar, add **Solar to Home Energy** under **Solar panels**.

> Note: The API provides interval measurements (not cumulative totals). For precise long-term tracking, pair this integration with a `utility_meter` helper in Home Assistant.

---

## Troubleshooting

### "Could not connect to Base Power"
- Check your internet connection.
- Confirm `dashboard.baseapis.net` is reachable from your HA host.

### "The verification code was incorrect"
- OTP codes expire quickly. Request a fresh one by re-starting the setup flow.
- Make sure you are using the most recent code from your inbox.

### Sensors show "Unavailable" after setup
- This is normal for brand-new installs. Some data (billing cycles, grid status) only appears once the system has been active for at least one billing period.
- Check **Settings → System → Logs** for `base_power` entries for more detail.

### Re-authentication loop
- If you are repeatedly prompted to sign in, your Base Power account session may have been revoked (e.g., you signed out from the app). Complete the sign-in flow again to issue a new session.

---

## Contributing

Contributions are welcome! Please:

1. Fork the repo and create a feature branch.
2. Follow [Home Assistant development guidelines](https://developers.home-assistant.io/docs/development_index).
3. Open a pull request with a clear description of the change.

### Reporting Issues

Open an issue on [GitHub](https://github.com/jerrit/ha-base-power/issues) and include:
- Home Assistant version
- Integration version
- Relevant log lines (Settings → System → Logs, filter by `base_power`)
- Description of what you expected vs. what happened

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Acknowledgements

- The [Home Assistant](https://www.home-assistant.io/) team for the incredible platform.
- The [HACS](https://hacs.xyz/) project for making custom integrations easy to distribute.
- The Base Power community for inspiring this work.
